### Title
Fallback `totalIn1Scaled` override charges full remaining input without updating `out0Scaled`, causing trader to receive less token0 than paid for — (`metric-core/contracts/libraries/SwapMath.sol`)

---

### Summary

In `buyToken0InBinSpecifiedIn`, a two-pass refinement loop attempts to find the correct `targetPos` when the analytical closed-form solution undershoots. A final fallback at lines 620-622 forcibly sets `totalIn1Scaled = state.amountSpecifiedRemainingScaled` without updating `out0Scaled`, breaking the swap conservation invariant: the pool consumes the full remaining input but delivers token0 computed for a cheaper (lower) position.

---

### Finding Description

The function enters the `else` branch (line 579) only when the full bin up to `maxFinalBinPos` costs **more** than `remaining`, so the swap must partially consume the bin. The refinement sequence is:

**Step 1 — Analytical target** (lines 581-596):
`computeAnalyticalTargetPosForBuyToken0` returns a `targetPos` via a closed-form quadratic. `out0Scaled` and `totalIn1Scaled` are computed for that position.

**Step 2 — First linear refinement** (lines 598-618):
If `totalIn1Scaled < remaining && targetPos < maxFinalBinPos`, `targetPos` is scaled up linearly by `remaining / totalIn1Scaled`. `out0Scaled` and `totalIn1Scaled` are recomputed for the new `targetPos`.

**Step 3 — Fallback override** (lines 620-622):
```solidity
if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
    totalIn1Scaled = state.amountSpecifiedRemainingScaled;
}
```
`out0Scaled` is **not updated**. After this, `totalIn1Scaled == remaining` exactly, so the downscale guard at line 624 (`totalIn1Scaled > remaining`) does not fire.

**Settlement** (lines 639-644):
```solidity
binState.token0BalanceScaled -= out0Scaled.toUint104();          // delivers old, cheaper out0
binState.token1BalanceScaled += totalIn1Scaled - protocolFee;    // absorbs full remaining input
state.amountSpecifiedRemainingScaled -= totalIn1Scaled;          // fully consumed
state.amountCalculatedScaled += out0Scaled;                      // trader credited less token0
```

The pool absorbs `remaining` token1 but releases only the `out0Scaled` that was priced for a lower `targetPos`. The gap `remaining - old_totalIn1Scaled` is retained by the pool as surplus token1 with no corresponding token0 output.

**Why Step 3 is reachable:**

The cost function `totalIn1Scaled(targetPos)` is convex in `targetPos` because `avgPrice` increases with position (price rises as the bin is consumed buying token0). The linear scaling in Step 2 (`scaledDelta = ceil(delta * remaining / totalIn1Scaled)`) is a first-order approximation that systematically undershoots when the curve is strongly convex. After Step 2, `totalIn1Scaled` can still be less than `remaining` with `targetPos < maxFinalBinPos`, triggering the fallback.

---

### Impact Explanation

Every exact-input token1→token0 swap that hits this code path results in the trader paying the full `remaining` input while receiving less token0 than the bin curve permits. The shortfall is retained by the pool as excess token1 balance, accruing to LPs. This is a direct, per-swap loss of trader principal — a swap conservation failure where `amountOut * avgPrice < amountIn charged`.

---

### Likelihood Explanation

The condition requires the analytical solution to undershoot twice: once before Step 2 and once after. This occurs when the bin's price curve is strongly convex over the swap range — a normal condition for bins with large `lengthE6` or when `currBinPos` is far from `maxFinalBinPos`. No special permissions, malicious setup, or non-standard tokens are required. Any public caller invoking an exact-input token1→token0 swap through `MetricOmmPool` can trigger this.

---

### Recommendation

When the fallback at lines 620-622 fires, `out0Scaled` must be updated to reflect the full `remaining` input. The correct fix is to recompute `targetPos` and `out0Scaled` using the same proportional scaling already applied in Step 2, or to set `targetPos = maxFinalBinPos` and recompute `out0Scaled` accordingly (since the condition `targetPos < maxFinalBinPos` is still true, the bin has room). At minimum, `out0Scaled` must be rescaled proportionally to `remaining / old_totalIn1Scaled` before the fallback exits, mirroring the logic at lines 631-632.

---

### Proof of Concept

Foundry property test sketch:

```solidity
// Craft a bin where the analytical target undershoots twice:
// - large bin (lengthE6 = 1e5), token0BalanceScaled = 1e24
// - lowerPriceX64 = Q64, upperPriceX64 = Q64 * 2
// - currBinPos = 0, maxFinalBinPos = type(uint104).max
// - amountSpecifiedRemainingScaled chosen so totalIn1Scaled(analyticalTarget) < remaining
//   AND totalIn1Scaled(linearRefinement) < remaining

(, uint256 out0, , ,) = SwapMath.buyToken0InBinSpecifiedIn(binState, 0, state, 0, lower, upper, upper, 0);

uint256 consumed = initialRemaining - state.amountSpecifiedRemainingScaled;
// avgPrice at the returned targetPos
uint256 avgPrice = SwapMath.calculateArithmeticMean(startPrice, finalPrice);
uint256 expectedOut0 = (consumed << 64) / avgPrice;

// Assert: trader received less than the curve permits
assert(out0 < expectedOut0);
// Assert: full input was consumed
assert(consumed == initialRemaining);
```

The assertion `out0 < expectedOut0` will hold whenever lines 620-622 fire, confirming the conservation violation.

---

**Affected code:** [1](#0-0) 

The fallback sets `totalIn1Scaled` to full remaining without updating `out0Scaled`. [2](#0-1) 

Settlement uses the stale `out0Scaled` against the inflated `totalIn1Scaled`, producing the conservation gap. [3](#0-2) 

The first linear refinement that can still leave `totalIn1Scaled < remaining` due to the convexity of the cost function. [4](#0-3) 

Public entry point: any exact-input token1→token0 swap reaches `buyToken0InBinSpecifiedIn` through the pool's swap loop.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L598-618)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
          if (totalIn1Scaled == 0) totalIn1Scaled = 1;
          uint256 delta = targetPos - currBinPos;
          // remaining > totalIn1Scaled ⇒ scaledDelta > delta, may exceed MAX_POS_BIN → keep uint256
          uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn1Scaled);
          if (scaledDelta == 0) scaledDelta = 1;
          uint256 scaledTarget = currBinPos + scaledDelta;
          if (scaledTarget > maxFinalBinPos) {
            targetPos = maxFinalBinPos;
          } else {
            // Safe: scaledTarget ≤ maxFinalBinPos ≤ MAX_POS_BIN
            targetPos = scaledTarget;
          }

          out0Scaled = calculateOutputToken0FromBinPosition(binState.token0BalanceScaled, currBinPos, targetPos);

          finalPriceX64 = calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, targetPos, Math.Rounding.Ceil);
          avgPriceX64 = calculateArithmeticMean(startingPriceX64, finalPriceX64);
          in1WithoutFeeScaled = calculateRequiredToken(out0Scaled, avgPriceX64);
          totalIn1Scaled = grossInputWithBinFeeCeil(in1WithoutFeeScaled, onePlusBuyFeeX64);
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L620-622)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L994-1004)
```text
          (curPosInBinCache, outToken0AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken0InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```
