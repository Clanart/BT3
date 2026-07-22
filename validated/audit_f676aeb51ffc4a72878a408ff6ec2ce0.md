### Title
Stale cursor-bounds check in `addLiquidityWeighted` allows sandwich attack to deposit at manipulated price — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`addLiquidityWeighted` performs its cursor-position safety check (`_validateBinAndBinPosition`) **before** the probe call that actually reads the pool state used to scale shares. An attacker can sandwich the transaction, moving the pool cursor outside the user's declared bounds between the validation and the probe, so the user's liquidity is deposited at a price they explicitly tried to exclude.

---

### Finding Description

The function executes three sequential on-chain reads/writes in separate calls:

1. **Cursor validation** — `_validateBinAndBinPosition` reads `slot0` and reverts if the cursor is outside `[minimalCurBin, maximalCurBin]`.
2. **Probe** — `addLiquidity(…, KIND_PROBE, …)` is called; the pool always reverts with `LiquidityProbe(need0, need1)`, returning the token amounts the pool would require **at probe time**.
3. **Actual add** — `_addLiquidity` is called with shares scaled from the probe's `need0`/`need1`. [1](#0-0) 

The code comment on `addLiquidityWeighted` explicitly states the intent:

> *"Deposit composition follows the pool cursor at probe time; use slot0 cursor bounds to revert when state has been manipulated."* [2](#0-1) 

But the check is placed **before** the probe, not after it. The pool cursor can be moved by any swap between step 1 and step 2. Because the probe is a separate `try/call` that reads live pool state, `need0`/`need1` reflect the **manipulated** cursor, not the cursor the user validated. The actual add then deposits at that manipulated cursor position.

The cursor-bounds validation itself: [3](#0-2) 

reads `slot0` once and returns. Nothing re-checks the cursor before `_addLiquidity` is called: [4](#0-3) 

The `maxAmountToken0`/`maxAmountToken1` caps only bound the **quantity** of tokens paid; they do not protect against depositing at a **wrong price**. The scaling logic treats a zero-need leg as unconstrained (`type(uint256).max`), so if the attacker moves the cursor to a single-sided bin, the user can be forced to deposit the full cap of one token at an adversarial price: [5](#0-4) 

---

### Impact Explanation

A user who calls `addLiquidityWeighted` with tight cursor bounds to avoid depositing at an unfavorable price receives no protection. An attacker can cause the user to deposit up to `maxAmountToken0` or `maxAmountToken1` of tokens into a bin at an adversarial price, then swap back, extracting value from the user's position through immediate impermanent loss. This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

Any unprivileged actor can execute this with a single sandwich transaction on any block. No special permissions, no malicious token, no trusted role is required. The only cost is swap fees for the two manipulating swaps, which are easily recovered from the victim's impermanent loss on a sufficiently large deposit.

---

### Recommendation

Move `_validateBinAndBinPosition` to **after** the probe returns `need0`/`need1`, immediately before `_addLiquidity` is called. This ensures the cursor check reflects the same pool state that determined the deposit composition:

```solidity
// After decoding need0, need1 from the probe:
_validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
LiquidityDelta memory scaled = _scaleWeightsToShares(...);
return _addLiquidity(...);
```

Alternatively, re-read and re-validate `slot0` inside `_addLiquidity` before the paying call, so the check and the deposit are atomic within the same external call frame.

---

### Proof of Concept

1. Pool cursor is at bin `C` (within user's declared bounds `[A, B]`). User submits `addLiquidityWeighted` with `minimalCurBin = A`, `maximalCurBin = B`, `maxAmountToken0 = 10 000`, `maxAmountToken1 = 10 000`.
2. `_validateBinAndBinPosition` reads cursor = `C` ∈ `[A, B]` → passes.
3. Attacker frontruns with a large swap moving the cursor to bin `D` (outside `[A, B]`, single-sided: only `token0` needed).
4. Probe executes: pool returns `LiquidityProbe(need0=10 000, need1=0)`.
5. `_scaleWeightsToShares`: `scaleWad1 = type(uint256).max` (need1 == 0), `scaleWad = scaleWad0 = max0/need0 = 1 WAD`. Shares are unchanged.
6. `_addLiquidity` deposits at cursor `D`: pool requests `10 000 token0`, `0 token1`. Callback pays `10 000 token0`.
7. Attacker backruns, swapping back to cursor `C`. User's `10 000 token0` is now in a bin priced at `D`, suffering immediate impermanent loss equal to the price difference between `C` and `D`.

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L86-88)
```text
  ///      unchanged; the second call uses scaled integer shares. Deposit composition follows the pool cursor at
  ///      probe time; use slot0 cursor bounds to revert when state has been manipulated.
  function addLiquidityWeighted(
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L104-115)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L231-233)
```text
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;
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
