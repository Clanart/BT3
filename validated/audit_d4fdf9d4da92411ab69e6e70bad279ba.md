## Finding [1](#0-0) [2](#0-1) 

### Title
SimplexPaymaster never checks Chainlink `minAnswer`/`maxAnswer` bounds, letting a clamped depeg price under-price gas and drain paymaster funds - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster._getOraclePrice` only validates that the Chainlink `answer` is positive and not stale; it never checks whether the underlying aggregator has hit its `minAnswer`/`maxAnswer` circuit-breaker bounds. If a registered ERC-20 fee token's price feed (e.g. a stablecoin like USDC/USDT) crashes below its aggregator's `minAnswer` floor — exactly the LUNA/depeg scenario the external report describes — Chainlink continues returning the clamped `minAnswer` instead of the real, near-zero price. `SimplexPaymaster` will treat that stale-but-"fresh" clamped value as ground truth and compute gas pricing from it.

### Finding Description
`_getOraclePrice` is the sole gate on oracle data used for ERC-4337 gas pricing: [2](#0-1) 

It reverts on `answer <= 0` and on staleness (`block.timestamp - updatedAt > maxOracleAge`), but performs no comparison of `answer` against the aggregator's configured `minAnswer`/`maxAnswer`. The minimal local interface used doesn't even declare these getters: [1](#0-0) 

`_tokenPrice` then feeds this unchecked value directly into the fee-token conversion formula used by every gas payment: [3](#0-2) 

`tokenPrice = (nativeUsd * 10^tokenDecimals * (10000+markupBps)) / tokenUsd`. If `tokenUsd` (the fee token's USD price) is clamped at `minAnswer` while the token's real market value has collapsed far below that floor, `tokenUsd` is artificially *inflated* relative to the true price, which makes the computed `tokenPrice` (tokens owed per wei of gas) artificially *low*. Every UserOp settled through `_fetchDetails` → `_tokenPrice` uses this deflated conversion rate.

### Impact Explanation
This directly maps to the "paymaster balances ... must move exactly once and only to the rightful beneficiary and amount" pivot. An attacker holding large quantities of the depegged/near-worthless fee token can:
1. Submit ERC-4337 UserOps with `paymasterData` referencing the crashed token.
2. Pay gas using tokens priced at the stale `minAnswer` floor rather than their real (near-zero) market value.
3. `SimplexPaymaster` prefunds real native gas from its EntryPoint deposit and only collects the massively under-valued token in return, draining the contract's native-asset reserves/treasury surplus for tokens worth a fraction of the price charged.

This is fund loss borne by the protocol/treasury, reachable by any unprivileged UserOp sender — no malicious relayer, oracle operator, or governance actor required, since the clamped price is the aggregator's legitimate (if degraded) on-chain behavior during a depeg, exactly as described in the source report.

### Likelihood Explanation
Stablecoin depegs and severe token crashes are a recurring real-world event (USDC/USDT depegs, LUNA-style collapses) and are explicitly the failure mode Chainlink's circuit breaker is designed to survive without reverting. `SimplexPaymaster` is designed to accept "any token with a Chainlink feed" (per its own docstring), increasing the chance a registered token experiences this condition. Because the check is purely a staleness/positivity check, the clamped price will pass validation indefinitely until governance manually deactivates the token via `DeactivateToken` — a reactive, not preventive, control.

### Recommendation
Extend `AggregatorV3Interface` to expose `minAnswer()`/`maxAnswer()` (or fetch them via the full Chainlink `AggregatorV2V3Interface`/proxy `aggregator()` call), and in `_getOraclePrice` revert if `answer <= minAnswer` or `answer >= maxAnswer` (with sensible epsilon), mirroring the recommendation in the source report. Alternatively, maintain a governance-configurable sanity band per token (min/max USD price) enforced independently of the feed's own reported bounds, so a clamped feed cannot silently misprice gas.

### Proof of Concept
1. Register a stablecoin `T` with a Chainlink feed via `RegisterToken`, `markupBps = 0`.
2. Simulate the feed's underlying asset crashing to $0.01 while the aggregator's `minAnswer` is configured at (say) $0.90 — the aggregator (per Chainlink's real design) keeps returning `answer = 0.90e8`, `updatedAt = block.timestamp` (fresh, not stale).
3. Call `getTokenPrice(T)` / submit a UserOp paying with `T`: `_getOraclePrice` returns `0.90e8` without reverting, since `answer > 0` and it's not stale.
4. `_tokenPrice` computes `tokenPrice` using `tokenUsd = 0.90e8` while `T`'s real value is `0.01e8` — the attacker pays ~90x less real value in `T` than the gas actually costs, draining the paymaster's native reserves per UserOp until governance intervenes.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L17-25)
```text
/// @notice Minimal Chainlink AggregatorV3 interface — no external dependency needed.
interface AggregatorV3Interface {
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);

    function decimals() external view returns (uint8);
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
