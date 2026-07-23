The code is confirmed. The fallback at lines 620-622 sets `totalIn1Scaled = state.amountSpecifiedRemainingScaled` without updating `out0Scaled`. After this, `totalIn1Scaled == remaining` exactly, so the downscale guard at line 624 (`totalIn1Scaled > remaining`) does not fire. Settlement at lines 639-644 then uses the stale `out0Scaled` (priced for a lower `targetPos`) while absorbing the full remaining input — a direct conservation failure.

Audit Report

## Title
Fallback `totalIn1Scaled` override charges full remaining input without updating `out0Scaled`, causing trader to receive less token0 than paid for — (`metric-core/contracts/libraries/SwapMath.sol`)

## Summary
In `buyToken0InBinSpecifiedIn`, a two-pass refinement loop attempts to find the correct `targetPos` when the analytical closed-form solution undershoots. The fallback at lines 620-622 forcibly sets `totalIn1Scaled = state.amountSpecifiedRemainingScaled` without updating `out0Scaled`. The pool then absorbs the full remaining token1 input but releases only the token0 computed for a cheaper (lower) position, breaking the swap conservation invariant.

## Finding Description
The `else` branch (line 579) is entered when the full bin up to `maxFinalBinPos` costs more than `remaining`, so the swap must partially consume the bin.

**Step 1 — Analytical target** (lines 581-596): `computeAnalyticalTargetPosForBuyToken0` returns a `targetPos`. `out0Scaled` and `totalIn1Scaled` are computed for that position.

**Step 2 — Linear refinement** (lines 598-618): If `totalIn1Scaled < remaining && targetPos < maxFinalBinPos`, `targetPos` is scaled up proportionally. `out0Scaled` and `totalIn1Scaled` are recomputed.

**Step 3 — Fallback override** (lines 620-622):
```solidity
if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
    totalIn1Scaled = state.amountSpecifiedRemainingScaled;
}
```
`out0Scaled` is **not updated**. After this, `totalIn1Scaled == remaining` exactly, so the downscale guard at line 624 (`totalIn1Scaled > remaining`) does not fire, and `out0Scaled` remains stale.

**Settlement** (lines 639-644):
```solidity
binState.token0BalanceScaled -= out0Scaled.toUint104();          // delivers stale, cheaper out0
binState.token1BalanceScaled += totalIn1Scaled - protocolFee;    // absorbs full remaining input
state.amountSpecifiedRemainingScaled -= totalIn1Scaled;          // fully consumed
state.amountCalculatedScaled += out0Scaled;                      // trader credited less token0
```

The pool absorbs `remaining` token1 but releases only the `out0Scaled` priced for a lower `targetPos`. The gap `remaining - old_totalIn1Scaled` is retained by the pool as surplus token1 with no corresponding token0 output.

**Why Step 3 is reachable:** The cost function `totalIn1Scaled(targetPos)` is convex in `targetPos` because `avgPrice` increases with position (price rises as the bin is consumed buying token0). The linear scaling in Step 2 (`scaledDelta = ceil(delta * remaining / totalIn1Scaled)`) is a first-order approximation that systematically undershoots when the curve is strongly convex. After Step 2, `totalIn1Scaled` can still be less than `remaining` with `targetPos < maxFinalBinPos`, triggering the fallback. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation
Every exact-input token1→token0 swap that hits this code path results in the trader paying the full `remaining` input while receiving less token0 than the bin curve permits. The shortfall is retained by the pool as excess token1 balance, accruing to LPs. This is a direct, per-swap loss of trader principal — a swap conservation failure where `amountOut * avgPrice < amountIn charged`. This meets the "Swap conservation failure: trader receives more than the oracle/bin curve permits or pool fails to receive owed input" impact gate (inverted: trader receives less than owed).

## Likelihood Explanation
The condition requires the analytical solution to undershoot twice: once before Step 2 and once after. This occurs when the bin's price curve is strongly convex over the swap range — a normal condition for bins with large `lengthE6` or when `currBinPos` is far from `maxFinalBinPos`. No special permissions, malicious setup, or non-standard tokens are required. Any public caller invoking an exact-input token1→token0 swap through `MetricOmmPool` can trigger this via the public entry point at `MetricOmmPool.sol` lines 994-1004.

## Recommendation
When the fallback at lines 620-622 fires, `out0Scaled` must be updated to reflect the full `remaining` input. The correct fix mirrors the downscale logic already present at lines 631-632: rescale `out0Scaled` proportionally to `remaining / old_totalIn1Scaled` before overriding `totalIn1Scaled`, or set `targetPos = maxFinalBinPos` and recompute `out0Scaled` accordingly (since `targetPos < maxFinalBinPos` is still true, the bin has room). At minimum:
```solidity
if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
    out0Scaled = (out0Scaled * state.amountSpecifiedRemainingScaled) / totalIn1Scaled;
    totalIn1Scaled = state.amountSpecifiedRemainingScaled;
}
```

## Proof of Concept
```solidity
// Craft a bin where the analytical target undershoots twice:
// - large bin (lengthE6 = 1e5), token0BalanceScaled = 1e24
// - lowerPriceX64 = Q64, upperPriceX64 = Q64 * 2
// - currBinPos = 0, maxFinalBinPos = type(uint104).max
// - amountSpecifiedRemainingScaled chosen so totalIn1Scaled(analyticalTarget) < remaining
//   AND totalIn1Scaled(linearRefinement) < remaining

uint256 initialRemaining = state.amountSpecifiedRemainingScaled;
(, uint256 out0, , ,) = SwapMath.buyToken0InBinSpecifiedIn(binState, 0, state, 0, lower, upper, upper, 0);

uint256 consumed = initialRemaining - state.amountSpecifiedRemainingScaled;
// Assert: full input was consumed
assert(consumed == initialRemaining);
// Assert: trader received less than the curve permits for the consumed input
uint256 avgPrice = SwapMath.calculateArithmeticMean(startPrice, finalPrice);
uint256 expectedOut0 = (consumed << 64) / avgPrice;
assert(out0 < expectedOut0);
```
The assertion `out0 < expectedOut0` holds whenever lines 620-622 fire, confirming the conservation violation.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L620-622)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L624-634)
```text
        if (totalIn1Scaled > state.amountSpecifiedRemainingScaled) {
          uint256 delta = targetPos - currBinPos;
          // remaining < totalIn1Scaled ⇒ ratio < 1 ⇒ scaledDelta ≤ delta ≤ MAX_POS_BIN
          uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn1Scaled);
          if (scaledDelta == 0) scaledDelta = 1;
          targetPos = currBinPos + scaledDelta;

          // Rescale out0Scaled proportionally; remaining < totalIn1Scaled ⇒ result ≤ out0Scaled ≤ MAX_POS_BIN
          out0Scaled = (out0Scaled * state.amountSpecifiedRemainingScaled) / totalIn1Scaled;
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L639-644)
```text
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn1Scaled;
      state.amountCalculatedScaled += out0Scaled;
```
