Based on my analysis of the files I've read, let me synthesize the findings.

---

### Title
`addLiquidityWeighted` Lacks Minimum-Shares-Received Guard, Enabling Sandwich Attack on LP Deposits — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`addLiquidityWeighted` uses a probe-then-add pattern to scale liquidity shares to the user's token budget. The only price-manipulation guard is a cursor-bounds check (`_validateBinAndBinPosition`) performed before the probe. Within those bounds an attacker can front-run the transaction, shift the pool cursor to a manipulated position, and cause the probe to return a token ratio that is unfavorable to the user. Because there is no minimum-shares-received parameter, the user's tokens are consumed at the manipulated price and the attacker profits on the back-run.

---

### Finding Description

`addLiquidityWeighted` executes the following sequence atomically within one transaction:

1. **Cursor-bounds check** — `_validateBinAndBinPosition` reads the live cursor and reverts if it falls outside `[minimalCurBin, maximalCurBin]`. [1](#0-0) 

2. **Probe call** — `addLiquidity` is called with `KIND_PROBE`; the callback reverts with `LiquidityProbe(need0, need1)`, revealing the token amounts the pool would require at the current cursor position. [2](#0-1) 

3. **Share scaling** — `_scaleWeightsToShares` computes `scaleWad = min(max0/need0, max1/need1)` and multiplies every weight by that factor to produce integer shares. [3](#0-2) 

4. **Paying add** — `_addLiquidity` is called with the scaled shares; the callback enforces `amount0Delta ≤ max0 && amount1Delta ≤ max1` before pulling tokens. [4](#0-3) 

**The gap**: the cursor-bounds check (step 1) only prevents the cursor from being *outside* the user-supplied range. It does not prevent the cursor from being *anywhere inside* that range. An attacker who front-runs the transaction and moves the cursor to an extreme position within the allowed range causes the probe (step 2) to return a `need0`/`need1` ratio that reflects the manipulated price. The scale factor (step 3) is then computed against that manipulated ratio, so the user deposits at the wrong price. The max-amount caps (step 4) prevent the user from paying *more* than their budget, but they do not guarantee a minimum number of shares received. After the attacker back-runs and restores the price, the user's position is worth less than the tokens they deposited.

There is no `minSharesReceived` (or equivalent) parameter anywhere in the function signature:

```solidity
function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,   // ← caps spending
    uint256 maxAmountToken1,   // ← caps spending
    int8 minimalCurBin,        // ← bounds cursor, not shares
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added)
``` [5](#0-4) 

By contrast, every swap entry-point in `MetricOmmSimpleRouter` carries an explicit output bound (`amountOutMinimum` / `amountInMaximum`): [6](#0-5) [7](#0-6) 

`addLiquidityWeighted` is the only user-facing entry point that accepts a token budget and computes shares dynamically, yet provides no analogous output bound.

---

### Impact Explanation

A user who calls `addLiquidityWeighted` with a wide cursor range (e.g., `minimalCurBin = -10, maximalCurBin = 10`) can be sandwiched:

- The attacker shifts the cursor to an extreme bin within the allowed range, skewing the probe's `need0`/`need1` ratio.
- The scale factor `min(max0/need0, max1/need1)` is computed against the skewed ratio, so the user receives fewer shares than they would at the fair price.
- The attacker restores the price; the user's position is now worth less than the tokens deposited.

The loss is bounded by the width of the cursor range and the user's max token caps, but it is a direct, unrecoverable loss of deposited principal — qualifying as a direct loss of user funds above the Sherlock Medium threshold.

---

### Likelihood Explanation

- `addLiquidityWeighted` is the primary convenience entry point for users who do not know the exact share amounts to deposit; it is expected to be called frequently.
- The attack requires only a standard front-run/back-run sandwich, which is trivially executable by any MEV searcher on any EVM chain.
- Users who specify wide cursor bounds (common for general-purpose deposits) are fully exposed; only users who set very tight bounds (e.g., a single bin) are effectively protected.

---

### Recommendation

Add a `minSharesPerBin` or aggregate `minTotalShares` parameter to `addLiquidityWeighted`. After the paying add completes, compare the actual shares minted against the user-supplied minimum and revert if the threshold is not met:

```solidity
function addLiquidityWeighted(
    ...
    uint256 minTotalSharesReceived,   // NEW: revert if shares < this
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    ...
    (amount0Added, amount1Added) = _addLiquidity(...);
    uint256 totalShares = /* sum of scaled.shares */;
    if (totalShares < minTotalSharesReceived) revert InsufficientSharesReceived(totalShares, minTotalSharesReceived);
}
```

Alternatively, expose the scaled shares computed in step 3 to the caller before the paying add, so the caller can verify them off-chain and pass them directly to `addLiquidityExactShares` instead.

---

### Proof of Concept

**Setup**: Pool with token0/token1, fair cursor at bin 0. User wants to deposit up to 1000 token0 and 1000 token1 with `minimalCurBin = -5, maximalCurBin = 5`.

1. **Attacker front-runs**: swaps a large amount of token0 → token1, moving the cursor to bin 5 (within the user's allowed range). At bin 5, the pool prices token0 cheaply relative to token1.

2. **User's `addLiquidityWeighted` executes**:
   - `_validateBinAndBinPosition`: cursor is at bin 5, within `[-5, 5]` → passes. [8](#0-7) 
   - Probe returns `need0 = 1800, need1 = 200` (skewed toward token0 at manipulated price).
   - Scale factor = `min(1000/1800, 1000/200) = 0.555` (limited by token0 cap).
   - Scaled shares are 55.5% of the weight vector.
   - User deposits ~555 token0 and ~111 token1 at the manipulated price.

3. **Attacker back-runs**: swaps token1 → token0, restoring cursor to bin 0. Attacker profits from the round-trip.

4. **Result**: At fair price (bin 0), the same 555 token0 + 111 token1 would have purchased significantly more shares. The user's position is worth less than deposited; the difference is extracted by the attacker. No revert occurs because there is no minimum-shares-received check. [9](#0-8)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
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
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L276-285)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-83)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-145)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
```
