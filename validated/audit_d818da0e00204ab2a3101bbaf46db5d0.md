Audit Report

## Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Stale Liquidity Additions at Unfavorable Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
All four public liquidity-adding functions in `MetricOmmPoolLiquidityAdder` accept no `deadline` parameter and perform no `block.timestamp` guard. A pending transaction can be held in the mempool and executed arbitrarily far in the future at pool conditions the user never intended to accept. The `maxAmountToken0`/`maxAmountToken1` caps bound token quantity but do not prevent execution at a stale price, leaving the user with a position worth less than the deposited tokens.

## Finding Description
`MetricOmmSwapRouterBase._checkDeadline` is implemented at `metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol` L91–94 and is called at the top of every swap entry point in `MetricOmmSimpleRouter` (L68, L93, L131, L155). `MetricOmmPoolLiquidityAdder` does not inherit `MetricOmmSwapRouterBase` and contains no equivalent guard.

The four affected entry points are:

- `addLiquidityExactShares(pool, owner, salt, deltas, maxAmountToken0, maxAmountToken1, extensionData)` — L56–68: no deadline, no cursor-range guard; executes unconditionally regardless of elapsed time or price movement.
- `addLiquidityExactShares(pool, salt, deltas, maxAmountToken0, maxAmountToken1, extensionData)` — L71–81: same, no deadline, no cursor-range guard.
- `addLiquidityWeighted(pool, owner, salt, weightDeltas, ..., minimalCurBin, minimalPosition, maximalCurBin, maximalPosition, extensionData)` — L88–116: calls `_validateBinAndBinPosition` (L104) which checks the live cursor against user-supplied bounds, but only reverts if the cursor has moved *outside* the window; execution anywhere inside the window at a price the user never intended is permitted.
- `addLiquidityWeighted(pool, salt, weightDeltas, ...)` — L123–149: same partial guard, no deadline.

For `addLiquidityExactShares` there is no cursor-range guard at all. The callback at L165 enforces `amount0Delta <= max0 && amount1Delta <= max1`, which caps token quantity but not the economic value of the resulting position. A transaction delayed by hours can deposit tokens into bins that are now deep out of range, producing a position worth materially less than the deposited tokens with no revert.

## Impact Explanation
A user who submits `addLiquidityExactShares` targeting the current bin during a gas-price spike may have their transaction mined much later after the pool cursor has shifted. Their tokens are deposited into bins at the wrong price level. The `maxAmount` caps are not exceeded — the pool simply consumes the tokens at the stale bin. The resulting position may be immediately and permanently out of range, locking principal at a loss relative to simply holding the tokens. This constitutes a direct loss of user principal meeting Sherlock medium thresholds: the user receives a liquidity position worth less than the tokens they deposited, with no on-chain recourse.

## Likelihood Explanation
Any period of mempool congestion (gas-price spike, network stress) causes transactions to queue. This is a routine occurrence on mainnet. No special attacker capability is required — a searcher or validator can simply delay inclusion. The user has no on-chain mechanism to cancel or protect themselves once the transaction is broadcast. The condition is common and repeatable.

## Recommendation
Add a `uint256 deadline` parameter to all four public functions and revert if `block.timestamp > deadline`, mirroring the pattern already used in `MetricOmmSwapRouterBase._checkDeadline`:

```solidity
error DeadlineExpired(uint256 deadline, uint256 current);

function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   if (block.timestamp > deadline) revert DeadlineExpired(deadline, block.timestamp);
    _validateOwner(owner);
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads and the second `addLiquidityExactShares` overload.

## Proof of Concept
1. User calls `addLiquidityExactShares` targeting bin index 0 (current price) with `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`.
2. Transaction is broadcast but not mined due to low gas price during network congestion.
3. Pool price moves: bin cursor shifts to bin index +5 (token0 now much cheaper relative to token1).
4. Transaction is mined hours later. The pool accepts the exact shares at bin 0, which is now deep out-of-range on the token0 side.
5. User's `1000e18` token0 is deposited into a bin that will never be crossed again at current prices. The position is worth significantly less than the deposited tokens.
6. No revert occurs: `maxAmountToken0` and `maxAmountToken1` were not exceeded — the pool consumed the tokens at the stale bin. The callback check at L165 passes because `amount0Delta <= max0`.

Foundry test skeleton:
```solidity
function test_addLiquidityExactShares_staleExecution() public {
    // warp time forward to simulate delayed mining
    vm.warp(block.timestamp + 3600);
    // pool price has moved; bin 0 is now out of range
    // call addLiquidityExactShares targeting bin 0 — succeeds with no revert
    // assert position is out of range and worth less than deposited tokens
}
```