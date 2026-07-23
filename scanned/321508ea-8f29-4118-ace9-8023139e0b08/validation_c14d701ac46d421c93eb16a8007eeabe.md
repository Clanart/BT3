The code is clear. Let me trace the exact execution path at lines 758-760 and verify the conservation invariant.

**The bug:** When the fallback at lines 758-760 fires, `totalIn0Scaled` is overwritten to `state.amountSpecifiedRemainingScaled` but `out1Scaled` is **not updated**. The pool then charges the full remaining input (line 783) while only delivering the output computed for the partial `targetPos` (line 784).

The symmetric function `buyToken0InBinSpecifiedIn` has the identical pattern at lines 620-622.

---

### Title
Swap Conservation Violation in `buyToken1InBinSpecifiedIn`: Fallback Override Charges Full Input Without Updating Output — (`metric-core/contracts/libraries/SwapMath.sol`)

### Summary
In `SwapMath.buyToken1InBinSpecifiedIn`, after the analytical solution and one linear-scaling refinement both undershoot (`totalIn0Scaled < state.amountSpecifiedRemainingScaled` with `targetPos > minFinalBinPos`), the fallback at lines 758-760 sets `totalIn0Scaled = state.amountSpecifiedRemainingScaled` without recomputing `out1Scaled`. The pool then charges the full remaining token0 input but delivers only the token1 output corresponding to the partial `targetPos`, violating the swap conservation invariant.

### Finding Description

The function resolves the partial-fill position through three stages:

**Stage 1 (lines 720-736):** Analytical closed-form solution computes `targetPos` and `totalIn0Scaled`.

**Stage 2 (lines 738-756):** If the analytical solution undershot (`totalIn0Scaled < remaining && targetPos > minFinalBinPos`), a linear-scaling refinement moves `targetPos` further toward `minFinalBinPos` and recomputes both `out1Scaled` and `totalIn0Scaled`.

**Stage 3 (lines 758-760):** If the refinement *still* undershoots, the fallback fires:

```solidity
if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
    totalIn0Scaled = state.amountSpecifiedRemainingScaled;
}
```

`out1Scaled` is **not updated** here. The subsequent settlement at lines 783-784 then charges `totalIn0Scaled` (= full remaining input) and credits `out1Scaled` (= output for the partial `targetPos`). [1](#0-0) 

The overshoot branch at lines 762-773 correctly handles the symmetric case by proportionally rescaling `out1Scaled` alongside `totalIn0Scaled`:

```solidity
out1Scaled = (out1Scaled * state.amountSpecifiedRemainingScaled) / totalIn0Scaled;
totalIn0Scaled = state.amountSpecifiedRemainingScaled;
``` [2](#0-1) 

But the undershoot fallback at 758-760 has no equivalent `out1Scaled` update. The same structural defect exists in `buyToken0InBinSpecifiedIn` at lines 620-622. [3](#0-2) 

**Why Stage 3 is reachable:** The refinement in Stage 2 uses a linear scaling of `delta` (the position distance). The actual cost function is non-linear — `calculateRequiredToken(out1Scaled, avgPriceX64)` depends on the arithmetic mean of two prices that are themselves non-linear functions of `targetPos`. In bins with wide price ranges or extreme `lowerPriceX64`/`upperPriceX64` ratios, one linear refinement step is insufficient to close the gap, leaving `totalIn0Scaled < remaining` after Stage 2. [4](#0-3) 

### Impact Explanation

When the fallback fires:
- `state.amountSpecifiedRemainingScaled -= totalIn0Scaled` consumes the **full remaining input** (line 783), so the outer swap loop exits after this bin.
- `state.amountCalculatedScaled += out1Scaled` credits only the **partial output** (line 784). [5](#0-4) 

The trader pays `state.amountSpecifiedRemainingScaled` token0 but receives `out1Scaled` token1, where `out1Scaled * avgInvertedPrice < totalIn0Scaled`. The excess token0 enters the bin's balance (line 780-781), accruing to LPs. This is a direct, permanent loss of trader principal — a swap conservation failure. [6](#0-5) 

### Likelihood Explanation

The condition requires: (a) the analytical solution undershoots, AND (b) one linear refinement still undershoots. This occurs in bins where the price curve non-linearity is large relative to the position step — e.g., bins with `upperPriceX64 / lowerPriceX64` ratios significantly above 1 and input amounts that land in the non-linear region of the quadratic cost curve. These are normal, permissionless pool configurations. No privileged setup is required; any trader swapping token0→token1 through such a bin can trigger it.

### Recommendation

Replace the fallback at lines 758-760 with a full traversal to `minFinalBinPos`, recomputing `out1Scaled` and `totalIn0Scaled` consistently:

```solidity
if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
    // Two refinements still undershoot: consume the full bin to minFinalBinPos.
    targetPos = minFinalBinPos;
    out1Scaled = calculateOutputToken1FromBinPosition(binState.token1BalanceScaled, currBinPos, targetPos);
    invertedFinalPriceX64 =
        invertPriceX64(calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, targetPos, Math.Rounding.Floor));
    avgPriceX64 = calculateArithmeticMean(invertedStartingPriceX64, invertedFinalPriceX64);
    in0WithoutFeeScaled = calculateRequiredToken(out1Scaled, avgPriceX64);
    totalIn0Scaled = grossInputWithBinFeeCeil(in0WithoutFeeScaled, onePlusSellFeeX64);
}
```

This ensures `out1Scaled` and `totalIn0Scaled` remain consistent. Apply the same fix to `buyToken0InBinSpecifiedIn` lines 620-622.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Foundry property test sketch
// Invariant: out1Scaled * avgInvertedPrice <= totalIn0Scaled charged

function testConservation_buyToken1_fallback(
    uint104 currBinPos,      // e.g. type(uint104).max / 2
    uint104 token1Balance,   // e.g. 1e24
    uint128 remaining,       // crafted to land in Stage 3
    uint128 lowerPriceX64,   // e.g. 1e14
    uint128 upperPriceX64    // e.g. lowerPriceX64 * 1000 (wide bin)
) public {
    BinState memory bin = BinState({
        token0BalanceScaled: 0,
        token1BalanceScaled: token1Balance,
        lengthE6: 1, addFeeBuyE6: 0, addFeeSellE6: 0
    });
    SwapMath.SwapState memory state = SwapMath.SwapState({
        amountSpecifiedRemainingScaled: remaining,
        amountCalculatedScaled: 0,
        protocolFeeAmountScaled: 0,
        feeExclusiveInputScaled: 0
    });

    (, uint256 out1Scaled,,,) = SwapMath.buyToken1InBinSpecifiedIn(
        bin, currBinPos, state, 0, lowerPriceX64, upperPriceX64, 0, 0
    );

    uint256 charged = remaining - state.amountSpecifiedRemainingScaled;
    uint256 invertedAvgPrice = /* compute avgInvertedPrice from lowerPriceX64, upperPriceX64, finalPos */;

    // Conservation invariant: output value at avg price <= input charged
    // Violation: out1Scaled * invertedAvgPrice > charged  (trader underpaid)
    // OR:        out1Scaled * invertedAvgPrice << charged  (trader overpaid — this bug)
    assertLe(
        charged,
        out1Scaled * invertedAvgPrice / (1 << 64) + TOLERANCE,
        "Trader overpaid: conservation violated"
    );
}
```

Target bin parameters: `upperPriceX64 / lowerPriceX64 >= 100`, `currBinPos` near `type(uint104).max / 2`, and `remaining` set to approximately `1.05 * totalIn0Scaled_after_analytical` to force Stage 2 to still undershoot after one linear refinement.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L620-622)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L738-756)
```text
        if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
          if (totalIn0Scaled == 0) totalIn0Scaled = 1;

          uint256 delta = currBinPos - targetPos;
          // remaining > totalIn0Scaled ⇒ scaledDelta > delta, may exceed MAX_POS_BIN → keep uint256
          uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn0Scaled);
          if (scaledDelta == 0) scaledDelta = 1;
          targetPos = currBinPos > scaledDelta ? currBinPos - scaledDelta : 0;
          if (targetPos < minFinalBinPos) {
            targetPos = minFinalBinPos;
          }

          out1Scaled = calculateOutputToken1FromBinPosition(binState.token1BalanceScaled, currBinPos, targetPos);

          invertedFinalPriceX64 =
            invertPriceX64(calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, targetPos, Math.Rounding.Floor));
          avgPriceX64 = calculateArithmeticMean(invertedStartingPriceX64, invertedFinalPriceX64);
          in0WithoutFeeScaled = calculateRequiredToken(out1Scaled, avgPriceX64);
          totalIn0Scaled = grossInputWithBinFeeCeil(in0WithoutFeeScaled, onePlusSellFeeX64);
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L758-760)
```text
        if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
          totalIn0Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L770-772)
```text
          // Rescale out1Scaled proportionally; remaining < totalIn0Scaled ⇒ result ≤ out1Scaled ≤ MAX_POS_BIN
          out1Scaled = (out1Scaled * state.amountSpecifiedRemainingScaled) / totalIn0Scaled;
          totalIn0Scaled = state.amountSpecifiedRemainingScaled;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L779-784)
```text
      binState.token1BalanceScaled -= out1Scaled.toUint104();
      binState.token0BalanceScaled =
        (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn0Scaled;
      state.amountCalculatedScaled += out1Scaled;
```
