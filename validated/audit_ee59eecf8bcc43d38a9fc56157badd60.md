Audit Report

## Title
Missing Deadline Guard on All Four `MetricOmmPoolLiquidityAdder` Entry Points Allows Stale Oracle-Price Composition — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
`MetricOmmPoolLiquidityAdder` exposes four public liquidity-addition entry points — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — none of which accept or enforce a `deadline` timestamp. The sibling `MetricOmmSimpleRouter` calls `_checkDeadline` at the top of every swap entry point. A pending transaction that executes after the oracle price has moved will deposit the user's tokens at a composition determined by the new price, not the price the user observed when signing, causing an LP position centred around the wrong price with no recourse.

## Finding Description
`MetricOmmSimpleRouter` enforces a deadline on every public swap entry point:
- `exactInputSingle` at L68, `exactInput` at L93, `exactOutputSingle` at L131, `exactOutput` at L155 — all call `_checkDeadline(params.deadline)` before any pool interaction.

`MetricOmmPoolLiquidityAdder` has no equivalent guard on any of its four public entry points:
- `addLiquidityExactShares(address,address,uint80,…)` at L56–68
- `addLiquidityExactShares(address,uint80,…)` at L71–81
- `addLiquidityWeighted(address,address,uint80,…)` at L88–116
- `addLiquidityWeighted(address,uint80,…)` at L123–149

The `addLiquidityWeighted` flow is the most sensitive. It first executes a **probe** call (L106–112 / L139–145) that reverts inside the callback with `LiquidityProbe(need0, need1)`, where `need0`/`need1` reflect the pool cursor's oracle price **at execution time**. It then calls `_scaleWeightsToShares` (L226–243) to scale the user's weight vector to those amounts, and finally executes the paying deposit. If the transaction is delayed and the oracle price shifts, the probe runs at the new price, producing a completely different `need0`/`need1` ratio, and the user's tokens are deposited at that new ratio.

The only existing guard, `_validateBinAndBinPosition` (L263–286), reads `slot0` and reverts only if the cursor has moved **outside** the user-supplied `[minimalCurBin, maximalCurBin]` window. Any price movement that keeps the cursor inside that window — the common case for a user who sets a reasonable range — passes validation and executes at the stale composition.

## Impact Explanation
**Medium.** The user's token spend is bounded by `maxAmountToken0`/`maxAmountToken1`, so there is no unbounded drain. However, the composition of the deposit — how much token0 vs. token1 is taken — is determined by the oracle price at execution time, not at signing time. A significant price move within the cursor window causes the user to receive LP shares concentrated around the wrong oracle price, suffering immediate impermanent loss relative to their intended position. The loss is locked in once the liquidity is deposited and is non-trivial for any deposit above dust thresholds. This constitutes a direct loss of LP asset value, matching the allowed impact of "Medium direct loss of user principal or owed LP assets above Sherlock thresholds."

## Likelihood Explanation
**Low.** Requires the user to submit with a low gas price, the transaction to remain pending long enough for the oracle price to move materially within the user's cursor window, and the cursor to stay within the user's specified bounds. These conditions are uncommon but realistic during periods of network congestion or volatile markets. No privileged access or malicious setup is required; any unprivileged LP caller is affected.

## Recommendation
Add a `deadline` parameter to all four public entry points of `MetricOmmPoolLiquidityAdder` and call the same `_checkDeadline` helper used by the router at the top of each function, before any state reads or pool calls:

```solidity
function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   _checkDeadline(deadline);
    ...
}
```

Apply the same pattern to both `addLiquidityExactShares` overloads and both `addLiquidityWeighted` overloads.

## Proof of Concept
1. Alice calls `addLiquidityWeighted` when ETH/USDC oracle price is $2,000. She sets `maxAmountToken0 = 1e18`, `maxAmountToken1 = 2_000e6`, and cursor window `[-10, 10]`.
2. The transaction sits in the mempool for several hours due to low gas.
3. The oracle price drops to $1,500; the cursor stays within `[-10, 10]`, so `_validateBinAndBinPosition` does not revert.
4. The transaction executes. The probe runs at $1,500 and returns `need0 = 1e18`, `need1 = 1_500e6`.
5. `_scaleWeightsToShares` scales to fit within Alice's caps. The paying deposit pulls ~1 ETH and ~1,500 USDC.
6. Alice's LP position is now centred around $1,500. If ETH recovers to $2,000, she suffers impermanent loss compared to the position she intended to open at $2,000.
7. No deadline check exists to revert the transaction before step 4.

**Foundry test plan:** Deploy a mock pool whose oracle price can be set; call `addLiquidityWeighted` with a low gas price simulation; advance the oracle price within the cursor window; confirm the transaction executes and the deposited ratio reflects the new price rather than the original price.