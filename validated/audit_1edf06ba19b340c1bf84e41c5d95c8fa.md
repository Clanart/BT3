### Title
`addLiquidityWeighted` Lacks Minimum-Shares Guard, Enabling Front-Running to Drain LP Value — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`addLiquidityWeighted` in `MetricOmmPoolLiquidityAdder` uses a probe-then-pay pattern to determine token requirements and scale shares. It enforces a cursor-position bound (`_validateBinAndBinPosition`) but provides **no minimum-shares guarantee**. A front-runner can move the pool cursor to an extreme within the user's acceptable range before the transaction executes, inflating the probe's `need0`/`need1` values and causing the scale factor to collapse — the user spends up to their full token caps but receives a fraction of the expected LP shares.

---

### Finding Description

The two-step flow in `addLiquidityWeighted` is:

1. **Cursor check** — `_validateBinAndBinPosition` reads the live cursor and reverts if it is outside `[minimalCurBin, maximalCurBin]`.
2. **Probe** — a `KIND_PROBE` `addLiquidity` call always reverts with `LiquidityProbe(need0, need1)`, reporting how many tokens the weight-based shares would consume at the current pool state.
3. **Scale** — `_scaleWeightsToShares` computes `scaleWad = min(max0/need0, max1/need1)` and multiplies every weight by it.
4. **Pay** — `_addLiquidity` executes the real call with the scaled shares. [1](#0-0) 

The scale factor is entirely determined by the probe result: [2](#0-1) 

If `need0` is inflated, `scaleWad0 = max0/need0` shrinks, `scaleWad` shrinks, and every `shares[i]` shrinks proportionally. There is **no floor on the resulting shares**; the only revert is `SharesRoundedToZero` when a share rounds to exactly zero.

The cursor check is performed once, before the probe, and is not re-evaluated before the paying call: [3](#0-2) 

A front-runner who moves the cursor to the token0-heavy extreme of the user's acceptable range (still passing the bounds check) causes the probe to return a large `need0` and small `need1`. The binding leg becomes token0, `scaleWad` collapses, and the user's shares are a small fraction of what they would have been at the original cursor position.

---

### Impact Explanation

The user spends up to `maxAmountToken0` tokens but receives far fewer LP shares than expected. LP shares represent a proportional claim on pool liquidity; receiving fewer shares is a direct loss of principal. The loss magnitude scales with how far the attacker can move the cursor within the user's acceptable bounds — a user who specifies a wide cursor range (e.g., `minimalCurBin = -10`, `maximalCurBin = 10`) is maximally exposed.

**Example:**

| State | `need0` | `need1` | `scaleWad` | Shares received |
|---|---|---|---|---|
| Normal (cursor centred) | 500e18 | 500e18 | 2.0 WAD | 200% of weights |
| After front-run (cursor at token0 extreme) | 1 800e18 | 200e18 | 0.556 WAD | 55.6% of weights |

The user receives **27.8%** of the shares they expected while spending the same token0 budget.

---

### Likelihood Explanation

Any caller of `addLiquidityWeighted` who specifies a non-trivial cursor range is vulnerable. The attack requires only a single swap to move the cursor within the user's acceptable bounds — a standard sandwich. The attacker profits from the spread of the manipulating swap and can back-run to restore the cursor. No privileged access is required; the trigger is an ordinary pending transaction visible in the mempool.

---

### Recommendation

Add a `minTotalShares` parameter to both `addLiquidityWeighted` overloads and check it after `_scaleWeightsToShares`:

```solidity
// In IMetricOmmPoolLiquidityAdder / MetricOmmPoolLiquidityAdder

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
+   uint256 minTotalShares,   // <-- new parameter
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    // ... existing validation ...

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData)
    returns (uint256, uint256) {
        revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
        (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
        LiquidityDelta memory scaled =
            _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);

+       uint256 totalShares;
+       for (uint256 i; i < scaled.shares.length; i++) totalShares += scaled.shares[i];
+       if (totalShares < minTotalShares) revert InsufficientShares(totalShares, minTotalShares);

        return _addLiquidity(pool, owner, salt, scaled, msg.sender,
                             maxAmountToken0, maxAmountToken1, extensionData);
    }
}
```

This mirrors the fix proposed in the external report: give the caller an explicit floor on the value they receive, so any manipulation that would push shares below that floor causes a clean revert instead of a silent loss.

---

### Proof of Concept

1. **Setup:** Alice calls `addLiquidityWeighted` with `maxAmountToken0 = 1 000e18`, `maxAmountToken1 = 1 000e18`, cursor bounds `[-5, 5]`, and weight deltas centred around bin 0. At the current cursor (bin 0, centred), the probe would return `need0 ≈ need1 ≈ 500e18`, yielding `scaleWad ≈ 2 WAD` and doubling her weights into shares.

2. **Front-run:** Bob observes Alice's pending transaction. He swaps a large amount of token1 → token0 in the pool, moving the cursor to bin 5 (the token0-heavy extreme, still within Alice's `maximalCurBin = 5`). The cursor check will still pass.

3. **Alice's transaction executes:**
   - `_validateBinAndBinPosition` passes (cursor is at bin 5, within `[-5, 5]`).
   - Probe returns `need0 = 1 800e18`, `need1 = 200e18`.
   - `scaleWad0 = 1000/1800 ≈ 0.556 WAD`; `scaleWad1 = 1000/200 = 5 WAD`; `scaleWad = 0.556 WAD`.
   - Alice's shares are 55.6% of her weights — roughly **28% of what she expected** at the original cursor.
   - Alice spends ≈ 1 000e18 token0 and ≈ 111e18 token1 but holds a position worth far less than 1 111e18.

4. **Back-run:** Bob swaps back, restoring the cursor and pocketing the spread. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L100-116)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L226-243)
```text
  function _scaleWeightsToShares(LiquidityDelta calldata w, uint256 max0, uint256 max1, uint256 need0, uint256 need1)
    internal
    pure
    returns (LiquidityDelta memory out)
  {
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;

    uint256 n = w.binIdxs.length;
    out.binIdxs = new int256[](n);
    out.shares = new uint256[](n);
    for (uint256 i; i < n; i++) {
      out.binIdxs[i] = w.binIdxs[i];
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L263-286)
```text
  function _validateBinAndBinPosition(
    address pool,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition
  ) internal view {
    if (minimalCurBin > maximalCurBin) {
      revert CursorOutOfBounds(0, 0, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }

    (, int8 curBinIdx, uint104 curPosInBin,,,) = PoolStateLibrary._slot0(pool);

    int256 curBin = curBinIdx;
    if (curBin < minimalCurBin || curBin > maximalCurBin) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == minimalCurBin && curPosInBin < minimalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == maximalCurBin && curPosInBin > maximalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
  }
```
