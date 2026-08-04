## Title
Single global `maxOracleAge` applied to oracles with different real heartbeats lets `SimplexPaymaster` accept stale prices as valid, enabling underpriced gas payment - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster` uses one global staleness bound, `maxOracleAge`, to validate every Chainlink feed it consults — the `nativeOracle` and every registered token's `tokenOracle` — even though the contract's own documentation states that heartbeats vary drastically across the feeds it must support (e.g. ~27s for BSC stablecoins vs up to 24h for Base/Ethereum stablecoins). This is the exact bug class from the referenced report: a single constant staleness threshold applied to feeds that individually require a much tighter bound will let a genuinely stale price pass the staleness check for feeds with a shorter real heartbeat, while still functioning for the slow feed that set the bound.

### Finding Description
`_getOraclePrice` is the only staleness gate in the contract, and it checks every oracle against the same `maxOracleAge`: [1](#0-0) 

`maxOracleAge` is a single storage variable set once via governance (`_setParams`) and applied uniformly regardless of which oracle (native or any of N registered tokens) is being read: [2](#0-1) [3](#0-2) 

The deploy script's own comment confirms the contract must tolerate feeds with wildly different heartbeats within the *same* value: "Stablecoin feeds on Ethereum and Base run a 24h heartbeat; a buffer over 24h avoids transient StaleOraclePrice reverts on late pushes," while `Params.maxOracleAge`'s doc explicitly states "Chainlink heartbeats vary per chain (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h)": [4](#0-3) [5](#0-4) 

`_registerToken` accepts any oracle without recording or validating its individual heartbeat/deviation threshold — there is no per-token staleness bound, only the single global `maxOracleAge`: [6](#0-5) 

Because `nativeOracle` and every `tokenConfigs[token].tokenOracle` are checked with the exact same threshold, governance is forced into a single choice: (a) set `maxOracleAge` loose enough to avoid spurious reverts on the slowest-heartbeat feed it supports (e.g. 24h stablecoin heartbeat, as the deploy script defaults to `90_000` seconds ≈ 25h), which means any faster-heartbeat feed (e.g. a volatile native asset or a fast-updating token feed) can silently go stale for up to that same window and still be accepted as valid; or (b) set it tight for the fast feed, which will spuriously revert (DoS) the slow feed. There is no code path that lets different feeds use different thresholds, unlike the Chainlink-oracle-per-feed pattern the original report recommends as mitigation.

### Impact Explanation
`_tokenPrice` directly consumes both stale-checked oracle answers to compute the gas price charged to users in ERC-20 tokens: [3](#0-2) 

If the native-asset oracle (or any token oracle) is falsely accepted as fresh while actually stale — because `maxOracleAge` was sized for a different feed's heartbeat — `_fetchDetails`/`fetchDetails` will price UserOps using a stale rate: [7](#0-6) 

During a native-asset price move that exceeds the elapsed-but-still-"fresh" window, users can pay significantly less in the ERC-20 token than the actual current gas cost, draining the paymaster's ERC-20 balances/EntryPoint deposit relative to actual gas spent — a direct loss of funds from the paymaster/treasury to any ordinary UserOp sender, requiring no relayer, prover, or admin compromise.

### Likelihood Explanation
This does not require a malicious peer, relayer, or governance actor — any user submitting a UserOp during a window where the native or a token oracle price has moved materially but not yet exceeded the loosely-configured `maxOracleAge` can benefit. The deploy script's own default (`90_000` seconds, chosen specifically to tolerate a 24h-heartbeat stablecoin feed) demonstrates this is not a theoretical misconfiguration — it is the documented, intended default that simultaneously governs the native oracle, which may have a materially different/faster heartbeat.

### Recommendation
Store a per-oracle (per native asset and per registered token) staleness bound instead of one global `maxOracleAge`, mirroring the mitigation from the original report ("set a unique STALE_PRICE_THRESHOLD for each token/feed"). Extend `TokenConfig` with a `maxOracleAge` field set at `_registerToken` time, and add a dedicated `nativeMaxOracleAge` set in `_setParams`, each validated independently in `_getOraclePrice`.

### Proof of Concept
1. Governance deploys `SimplexPaymaster` on a chain where the native asset (e.g. BNB) oracle has a fast heartbeat (~seconds-to-minutes) but a registered stablecoin's oracle has a 24h heartbeat.
2. To avoid spurious `StaleOraclePrice` reverts on the stablecoin feed, `maxOracleAge` is set to `90_000` seconds (~25h), matching the deploy script default in `evm/script/DeploySimplexPaymaster.s.sol` line 20.
3. The native asset experiences a sharp price move; `nativeOracle.latestRoundData()` is not updated for several hours (well within its normal heartbeat tolerance but a large price delta has occurred).
4. `_getOraclePrice(nativeOracle, ...)` in `SimplexPaymaster.sol` lines 428-434 passes the staleness check (`block.timestamp - updatedAt <= 90_000`), returning the outdated `nativeUsd`.
5. `_tokenPrice` computes gas cost using the stale `nativeUsd`; a user submits a UserOp with `paymasterData` referencing the registered stablecoin and pays gas priced off the outdated native rate, paying materially less token than the true post-move cost — draining the difference from the paymaster's assets.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-86)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L118-129)
```text
    AggregatorV3Interface public nativeOracle;
    uint8 public nativeOracleDecimals;
    uint256 public maxOracleAge;
    uint256 public markupBps;
    address public treasury;

    mapping(address => TokenConfig) public tokenConfigs;

    /// @notice Set of registered token addresses (for enumeration).
    address[] public registeredTokens;

    uint256 public swapSlippageBps;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L241-260)
```text
    /// @dev Registers or updates a supported ERC-20 token with its token/USD feed.
    ///      Re-registering is also the recovery path for a misbehaving oracle.
    function _registerToken(address token, AggregatorV3Interface oracle) internal {
        if (token == address(0) || address(oracle) == address(0)) revert ZeroAddress();

        bool isNew = !tokenConfigs[token].active && address(tokenConfigs[token].tokenOracle) == address(0);

        tokenConfigs[token] = TokenConfig({
            tokenOracle: oracle,
            tokenOracleDecimals: oracle.decimals(),
            tokenDecimals: IERC20Metadata(token).decimals(),
            active: true
        });

        if (isNew) {
            registeredTokens.push(token);
        }

        emit TokenRegistered(token, address(oracle));
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L374-393)
```text
    function _fetchDetails(
        PackedUserOperation calldata userOp,
        bytes32 /* userOpHash */
    ) internal view override returns (uint256 validationData, IERC20 token, uint256 tokenPrice) {
        bytes calldata data = userOp.paymasterData();
        if (data.length < 21) revert InvalidPaymasterData(data.length);

        uint8 mode = uint8(data[0]);
        if (mode > 0x01) revert InvalidMode(mode);

        address tokenAddr = address(bytes20(data[1:21]));

        TokenConfig memory cfg = tokenConfigs[tokenAddr];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
        if (!cfg.active) revert TokenNotActive(tokenAddr);

        tokenPrice = _tokenPrice(cfg);
        token = IERC20(tokenAddr);
        validationData = 0; // no time-range restriction
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L419-424)
```text
    function _tokenPrice(TokenConfig memory cfg) internal view returns (uint256) {
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);

        return (nativeUsd * (10 ** cfg.tokenDecimals) * (10_000 + markupBps)) / (tokenUsd * 10_000);
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L426-442)
```text
    /// @dev Fetch a Chainlink price normalized to 8 decimals.
    ///      Reverts on stale or non-positive answers.
    function _getOraclePrice(AggregatorV3Interface oracle, uint8 oracleDecimals) internal view returns (uint256) {
        (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();

        if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
        if (block.timestamp - updatedAt > maxOracleAge) {
            revert StaleOraclePrice(address(oracle), updatedAt);
        }

        if (oracleDecimals < 8) {
            return uint256(answer) * (10 ** (8 - oracleDecimals));
        } else if (oracleDecimals > 8) {
            return uint256(answer) / (10 ** (oracleDecimals - 8));
        }
        return uint256(answer);
    }
```

**File:** evm/script/DeploySimplexPaymaster.s.sol (L15-21)
```text
        address nativeOracleAddr = config.get("NATIVE_ORACLE").toAddress();
        uint256 markupBps = vm.envOr("MARKUP_BPS", uint256(200)); // default 2%
        address treasury = vm.envOr("TREASURY", admin); // default to deployer
        // Stablecoin feeds on Ethereum and Base run a 24h heartbeat; a buffer over
        // 24h avoids transient StaleOraclePrice reverts on late pushes.
        uint256 maxOracleAge = vm.envOr("MAX_ORACLE_AGE", uint256(90_000));
        uint256 swapSlippageBps = vm.envOr("SWAP_SLIPPAGE_BPS", uint256(200)); // default 2%
```
