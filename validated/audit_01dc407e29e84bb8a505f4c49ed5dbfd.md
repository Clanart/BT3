## Analysis: Stale-Oracle Arbitrage Draining SimplexPaymaster Reserves

The Kelp report's core broken invariant is: **a Chainlink price feed is only guaranteed to update when its deviation threshold is crossed within its heartbeat window — never continuously — so a "not stale" price can still be several percent away from the true market rate, and an unprivileged actor can act against the mispriced side before the next update lands.**

The strongest local analog is `SimplexPaymaster`, a permissionless ERC-4337 paymaster that prices ERC-20 gas payments purely off two independently-polled Chainlink feeds with only a staleness bound — no deviation/circuit-breaker check.

### Title
Stale-but-"fresh" Chainlink price in SimplexPaymaster lets any UserOperation sender underpay gas and drain the paymaster's token/ETH reserves - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` computes the ERC-20 cost of gas as `tokenPrice = nativeUsd * 10^tokenDecimals * (1+markup) / tokenUsd`, where `nativeUsd` and `tokenUsd` are each pulled independently from Chainlink via `_getOraclePrice`, which only rejects a price if it is older than `maxOracleAge` [1](#0-0) . `maxOracleAge` is a single governance value shared by both the native/USD feed and every token/USD feed, and is bounded only by `MAX_ORACLE_AGE = 7 days` [2](#0-1) . The contract's own comment acknowledges heartbeats "vary per chain (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h)" [3](#0-2) , meaning a config tuned for stablecoin heartbeats is also silently applied to the volatile native-asset (ETH/BNB) feed, which moves several percent within that same window — exactly the Kelp deviation-arbitrage pattern.

### Finding Description
`_fetchDetails`, called on every `UserOperation` during paymaster validation, derives `tokenPrice` from `_tokenPrice(cfg)` [4](#0-3) , which in turn calls `_getOraclePrice` twice — once for `nativeOracle` and once for the token's `tokenOracle` [5](#0-4) . The only guard applied is:
```solidity
if (block.timestamp - updatedAt > maxOracleAge) revert StaleOraclePrice(...);
```
There is no bound on how far the *reported* price can be from the *current* market price — only on how long ago it was last pushed on-chain. Chainlink feeds only push a new round when price moves past the feed's deviation threshold within its heartbeat; if that threshold isn't crossed, `updatedAt` stays fixed while the real price continues to drift, exactly as described for the rETH/cbETH/stETH feeds in the Kelp report.

Because `maxOracleAge` applies identically to the volatile `nativeOracle` (ETH/BNB) and to stablecoin token oracles, a governance configuration that is reasonable for a stablecoin feed (e.g. 24h, matching the doc's own guidance) leaves the native-asset leg of the ratio stale for up to a day — long enough for ETH/BNB to move multiple percent. Any address can submit a `UserOperation` with `paymasterData` selecting a registered token; `_validatePaymasterUserOp`/`_fetchDetails` accept it as long as the token is `active` and both oracle reads are within `maxOracleAge` [6](#0-5) . No relayer, prover, or admin is involved — this is a public entrypoint reachable by any account with a bundler.

### Impact Explanation
When the stale `nativeUsd` value understates the true native-asset price (e.g. it hasn't caught up to a real pump), `tokenPrice` computed from it is also understated, so `PaymasterERC20._erc20Cost` charges the UserOperation sender fewer stablecoin units than the gas is actually worth in USD. The attacker repeatedly submits self-serving UserOperations (cheap, since `MAX_POST_OP_GAS_LIMIT` bounds postOp cost) that drain real value from the paymaster: it pays real gas out of its EntryPoint deposit while collecting an artificially discounted stablecoin amount. This is a direct, repeatable loss of protocol funds — the treasury's swept stablecoin surplus and EntryPoint deposit are worth systematically less than they should be. This matches the bounty's "stealing or loss of funds" and "transaction manipulation" impact classes.

### Likelihood Explanation
Likelihood is high in normal, expected operating conditions:
- No malicious relayer, oracle operator, or governance action is required — this is the intended, permissionless usage path (any UserOperation sender via `_fetchDetails`).
- The contract's own maximum staleness bound (`MAX_ORACLE_AGE = 7 days`) and documented per-chain guidance (up to 24h) both permit windows far longer than typical native-asset price volatility windows.
- Chainlink deviation-based feeds are standard behavior, not a compromised or malicious assumption — the same "feature not bug" argument Kelp made applies here, but here it directly costs the protocol its own reserves rather than just redistributing value among LPs.

### Recommendation
- Track and bound the *skew* between the two oracle timestamps (`updatedAt` of `nativeOracle` vs. `tokenOracle`), not just each one's absolute age, and reject pricing when they diverge too much in either updatedAt or answer.
- Reduce `MAX_ORACLE_AGE` for the native-asset feed independently of stablecoin token feeds (add a second, tighter `maxNativeOracleAge`), since native-asset feeds are far more volatile.
- Consider adding a TWAP/secondary price source cross-check (e.g. the Uniswap-based `swapAndDeposit` pricing already used elsewhere in the same contract) as a sanity bound on the Chainlink spot price used for user-facing pricing.

### Proof of Concept
1. Governance configures `maxOracleAge = 24 hours` (within `_setParams`'s allowed range, and consistent with the contract's own doc comment for Base/Ethereum stablecoins) [7](#0-6) .
2. `nativeOracle` (ETH/USD) last updated 20 hours ago at price P0; the real market ETH price has since risen 3% to P1, but the feed's deviation threshold hasn't been crossed so no new round exists.
3. Attacker submits a `UserOperation` with `paymasterData` mode `0x01` selecting a registered stablecoin. `_fetchDetails` calls `_tokenPrice` → `_getOraclePrice(nativeOracle, ...)` returns P0 (accepted since `20h < 24h`) [8](#0-7) .
4. `tokenPrice = P0 * 10^tokenDecimals * (1+markup) / tokenUsd` is computed using the understated `P0` instead of the true `P1`, so `PaymasterERC20._erc20Cost` charges ~3% less stablecoin than the gas actually costs at the real ETH price.
5. Attacker repeats across many UserOperations until the feed updates, extracting the discount each time directly from the paymaster's stablecoin/ETH reserves.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-86)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L102-103)
```text
    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L213-239)
```text
    /// @dev Validates and applies pricing/treasury parameters, re-caching the
    ///      native oracle decimals.
    function _setParams(Params memory p) internal {
        if (address(p.nativeOracle) == address(0)) revert ZeroAddress();
        if (p.treasury == address(0)) revert ZeroAddress();
        if (p.markupBps > MAX_MARKUP_BPS) revert InvalidMarkup(p.markupBps);
        if (p.maxOracleAge == 0 || p.maxOracleAge > MAX_ORACLE_AGE) revert InvalidOracleAge(p.maxOracleAge);
        if (p.swapSlippageBps > MAX_SWAP_SLIPPAGE_BPS) revert InvalidSlippage(p.swapSlippageBps);

        emit ParamsUpdated(
            Params({
                nativeOracle: nativeOracle,
                markupBps: markupBps,
                treasury: treasury,
                maxOracleAge: maxOracleAge,
                swapSlippageBps: swapSlippageBps
            }),
            p
        );

        nativeOracle = p.nativeOracle;
        nativeOracleDecimals = p.nativeOracle.decimals();
        markupBps = p.markupBps;
        treasury = p.treasury;
        maxOracleAge = p.maxOracleAge;
        swapSlippageBps = p.swapSlippageBps;
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L342-393)
```text
    function _validatePaymasterUserOp(
        PackedUserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 maxCost
    ) internal override returns (bytes memory context, uint256 validationData) {
        uint256 postOpGasLimit = userOp.paymasterPostOpGasLimit();
        if (postOpGasLimit > MAX_POST_OP_GAS_LIMIT) {
            revert InvalidPostOpGasLimit(postOpGasLimit, MAX_POST_OP_GAS_LIMIT);
        }

        bytes calldata data = userOp.paymasterData();
        if (data.length == 0) revert InvalidPaymasterData(0);
        if (uint8(data[0]) == 0x00) {
            if (data.length < 21) revert InvalidPaymasterData(data.length);
            address tokenAddr = address(bytes20(data[1:21]));
            TokenConfig memory cfg = tokenConfigs[tokenAddr];
            if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
            if (!cfg.active) revert TokenNotActive(tokenAddr);
            _executePermit(userOp);
        }

        return super._validatePaymasterUserOp(userOp, userOpHash, maxCost);
    }

    /// @dev Returns the token to charge and its price relative to native gas.
    ///
    ///      PaymasterERC20 computes `erc20Cost = weiCost * tokenPrice / 1e18`,
    ///      so tokenPrice must be token base units per wei, scaled by 1e18:
    ///        tokenPrice = (nativeUsd * 10^tokenDecimals) / tokenUsd
    ///      e.g. BNB at $600, USDC at $1 with 6 decimals: 0.001 BNB (1e15 wei)
    ///      should cost 0.60 USDC (600000 units), giving tokenPrice = 6e8, which
    ///      is exactly (600e8 * 1e6) / 1e8. Markup is applied on top.
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
