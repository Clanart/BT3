Audit Report

## Title
Missing deadline protection on all four `MetricOmmPoolLiquidityAdder` liquidity entry points allows stale-price deposits - (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-add entry points (`addLiquidityExactShares` × 2, `addLiquidityWeighted` × 2) with no `deadline` parameter and no expiry check. The sibling `MetricOmmSimpleRouter` enforces `_checkDeadline` at the top of every swap entry point. A pending `addLiquidity` transaction that sits in the mempool will execute against whatever oracle price is live at inclusion time, depositing the LP's tokens at a price they never approved and causing immediate impermanent loss relative to their intended entry price.

## Finding Description

`MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` at the top of `exactInputSingle` (L68), `exactInput` (L93), `exactOutputSingle` (L131), and `exactOutput` (L155). The helper is defined in `MetricOmmSwapRouterBase` at L91–94:

```solidity
function _checkDeadline(uint256 deadline) internal view {
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
}
```

`MetricOmmPoolLiquidityAdder` does not inherit from `MetricOmmSwapRouterBase` and contains no equivalent check anywhere in its public surface. All four entry points (`addLiquidityExactShares` at L56–68 and L71–81, `addLiquidityWeighted` at L88–116 and L123–149) accept no `deadline` parameter and perform no timestamp validation.

For `addLiquidityExactShares`, the token amounts the pool requests in the callback are computed from the live oracle price at execution time. The only guard is the `maxAmountToken0`/`maxAmountToken1` cap enforced in `metricOmmModifyLiquidityCallback` at L165–167, which bounds total spend but does not constrain the price ratio at which tokens are deposited.

For `addLiquidityWeighted`, the probe-and-scale flow reads the pool cursor at execution time. The `_validateBinAndBinPosition` check at L263–286 only verifies the bin index (`curBinIdx`) against `minimalCurBin`/`maximalCurBin`; it does not check the oracle price within the bin. The oracle price can move substantially inside a single bin without triggering `CursorOutOfBounds`. The `_scaleWeightsToShares` function at L226–243 then scales shares to the new composition derived from the updated oracle price.

Exploit path:
1. Alice submits `addLiquidityWeighted` with cursor bounds `[bin 0, bin 0]` and `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`. The oracle bid/ask at submission time implies a 50/50 token split.
2. The transaction sits in the mempool. The oracle price is updated, moving the mid-price from 1.00 to 1.30 while the cursor remains in bin 0 (no bin crossing, so `CursorOutOfBounds` does not fire).
3. The transaction is included. The probe now sees the new oracle price; `_scaleWeightsToShares` scales shares to the new composition — e.g., 80% token0, 20% token1.
4. Alice's deposit executes at the 1.30 price. Her position immediately has impermanent loss relative to the 1.00 price she intended, with no recourse.

## Impact Explanation

The LP's tokens are deposited at an oracle price they did not approve, causing immediate impermanent loss relative to their intended entry price. This constitutes a direct loss of deposited principal. The `maxAmountToken0`/`maxAmountToken1` caps do not prevent this: they only bound total spend, not the price ratio at which the deposit occurs. The impact is a Medium-severity direct loss of user principal, consistent with Sherlock thresholds for missing slippage/deadline protection in periphery routers.

## Likelihood Explanation

Any period of network congestion or gas-price volatility can delay a pending `addLiquidity` transaction. Oracle prices in Metric OMM are updated externally; a price update between transaction submission and inclusion is sufficient to trigger the loss. No privileged access is required — the victim is any ordinary LP using the periphery adder. The conditions (mempool delay + oracle update) are routine in volatile markets.

## Recommendation

Add a `uint256 deadline` parameter to all four public functions in `MetricOmmPoolLiquidityAdder` and call an equivalent inline check `require(block.timestamp <= deadline, "expired")` (or inherit `MetricOmmSwapRouterBase` and call `_checkDeadline(deadline)`) at the top of each function, mirroring the pattern already used in `MetricOmmSimpleRouter` at L68, L93, L131, and L155.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry fork test outline:
// 1. Deploy pool with oracle at price 1.00 (bin 0).
// 2. Alice calls addLiquidityWeighted with bin bounds [0,0], max0=1000e18, max1=1000e18.
//    Record expected 50/50 split from probe at price 1.00.
// 3. vm.warp(block.timestamp + 300); // simulate mempool delay
// 4. Push oracle price to 1.30 (still within bin 0, no bin crossing).
// 5. Execute Alice's pending tx (no deadline check fires).
// 6. Assert actual token0 deposited > 600e18 (skewed toward token0 at new price).
// 7. Assert Alice's position has immediate IL vs. 1.00 entry price.
// Without deadline, step 5 succeeds; with deadline added and set to original block.timestamp,
// step 5 reverts with TransactionExpired.
```