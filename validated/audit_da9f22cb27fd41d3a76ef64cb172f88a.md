## Title
Bounded Chainlink staleness window lets a single stale round be replayed to drain SimplexPaymaster gas sponsorship - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
The `SimplexPaymaster` accepts any Chainlink round whose `updatedAt` is up to `maxOracleAge` (configurable up to `MAX_ORACLE_AGE = 7 days`) old, and uses that single un-bounded-magnitude answer directly to price gas for every `UserOperation` until the oracle updates again [1](#0-0) . This is the same broken invariant as the external report: a delayed/aged price feed value is trusted for a security/financial-critical computation for an extended window, instead of requiring the current price, letting a transient bad price be economically exploited for the entire staleness window.

### Finding Description
`_getOraclePrice` only checks that the last Chainlink round is not older than `maxOracleAge`; it performs no deviation check, no minimum-round-freshness requirement, and does not compare against a prior observation: [1](#0-0) 

`_tokenPrice`, which is invoked on every `_validatePaymasterUserOp` → `_fetchDetails` call (the permissionless ERC-4337 validation path executed by the EntryPoint for *any* UserOp), uses this value verbatim to compute the ERC-20 amount charged for gas: [2](#0-1) [3](#0-2) 

`maxOracleAge` is fully governance-configurable up to a hard ceiling of 7 days (`MAX_ORACLE_AGE`), enforced only as an upper bound, not a tight freshness requirement: [4](#0-3) [5](#0-4) 

Because `latestRoundData()` simply returns the last committed answer until a new round is pushed, any single abnormal round (a flash-crash tick, an exchange-outage misprint, or any transient bad print that Chainlink nonetheless commits) remains "valid" per the staleness check for the *entire* `maxOracleAge` window — exactly mirroring the report's core flaw: using an aged low/abnormal value for a financially decisive computation instead of the current price, and giving any unprivileged actor a multi-hour-to-multi-day window to exploit it.

`swapAndDeposit`'s minimum-output guard uses the identical unbounded `_getOraclePrice` for both legs, so it inherits the same staleness exposure, though it's treasury-gated. The `_fetchDetails`/`_validatePaymasterUserOp` path, by contrast, is fully permissionless: any address can submit a `PackedUserOperation` (mode `0x01 APPROVE`, using a pre-approved registered token) and have the EntryPoint invoke this pricing logic with no additional authorization.

### Impact Explanation
If a registered token's price feed prints one stale-but-in-bounds round that understates `tokenUsd` (or overstates `nativeUsd`) relative to the token's real value, `_tokenPrice` computes an artificially low `tokenPrice`. Every UserOp routed through the paymaster during the remainder of the staleness window then pays ERC-20 tokens far below the real USD value of the native gas the paymaster's EntryPoint deposit covers. The paymaster (funded by the treasury) is effectively drained: it dispenses real native gas while collecting undervalued ERC-20 tokens, a direct loss of sponsor funds to an unauthorized/unprivileged party, matching the bounty's "stealing or loss of funds" and "logic attack" categories.

### Likelihood Explanation
The exploit requires no compromised relayer, prover, admin, or governance actor — only an ordinary UserOp sender waiting for/observing one stale round from a registered token's Chainlink feed and then submitting operations through the fully permissionless `_validatePaymasterUserOp`/`_fetchDetails` path before the round ages past `maxOracleAge`. Since `MAX_ORACLE_AGE` allows configurations up to 7 days, and registered tokens are explicitly documented as "any token with a Chainlink feed" (including tokens with thinner liquidity/less robust feeds than USDC/USDT), the staleness window realistically available for exploitation can be large, and detection only occurs after funds have already left the paymaster.

### Recommendation
- Lower the effective staleness bound (or require freshness proportional to feed heartbeat per token) instead of a single governance-wide ceiling of up to 7 days.
- Add a sanity/deviation check against a recent TWAP or a secondary oracle before trusting a single `latestRoundData()` answer for pricing.
- Consider capping the maximum ERC-20 discount per UserOp (an oracle-price circuit breaker) so a single bad round cannot be repeatedly exploited across many UserOps within the staleness window.

### Proof of Concept
1. Governance registers a token with `maxOracleAge` near the allowed ceiling (up to 7 days) via `UpdateParams`/`RegisterToken` [6](#0-5) .
2. The token's Chainlink feed commits one abnormal, understated `answer` (e.g., a transient flash-crash print) that is not immediately updated.
3. Any address submits a `PackedUserOperation` with `paymasterData` mode `0x01` for that token; `_fetchDetails` computes `tokenPrice = _tokenPrice(cfg)` using the stale round via `_getOraclePrice`, which only checks `block.timestamp - updatedAt <= maxOracleAge` [7](#0-6) .
4. `PaymasterERC20._erc20Cost` charges the sender based on this understated `tokenPrice`, while the EntryPoint pays out real native gas from the paymaster's deposit.
5. Repeat across many UserOps until the feed updates or `maxOracleAge` elapses, draining the paymaster's sponsored gas balance at a loss to the treasury.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L99-106)
```text
    /// @dev Hard cap on the governance-configurable markup (50%).
    uint256 public constant MAX_MARKUP_BPS = 5_000;

    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;

    /// @dev Hard cap on the governance-configurable swap slippage (10%).
    uint256 public constant MAX_SWAP_SLIPPAGE_BPS = 1_000;
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

**File:** evm/src/utils/SimplexPaymaster.sol (L366-393)
```text
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
