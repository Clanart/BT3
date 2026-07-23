The code is present and I have enough to render a definitive verdict. Let me trace the exact arithmetic.

---

### Title
`_fillBids` subtracts the wrong bin's `lengthE6` when walking down the bid ladder, producing systematically wrong `lowerX64`/`upperX64` for every sub-current bin — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

---

### Summary

`_fillBids` walks the bid ladder by subtracting `lenAbove` — the `lengthE6` of bin **b+1** — to advance `walkDistE6`. The correct step is to subtract the `lengthE6` of bin **b** (the bin being computed). For non-uniform bin lengths this produces a shifted price window for every bin below `curBinIdx`, making every `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` in the bid ladder wrong.

---

### Finding Description

`_fillBids` initialises `walkDistE6 = curBinDistFromProvidedPriceE6`, which is the **lower edge** of `curBinIdx` (confirmed by the pool's own bin-walk at line 1200 of `MetricOmmPool.sol`). [1](#0-0) 

For each lower bin `b`, the correct lower edge is:

```
lowerEdge(b) = lowerEdge(b+1) − lengthE6(b)
```

But the loop reads `lenAbove` from bin **b+1** and subtracts that: [2](#0-1) 

Then it uses `walkDistE6` as the lower edge and `walkDistE6 + lengthE6(b)` as the upper edge: [3](#0-2) 

**Concrete arithmetic (first loop iteration, b = curBinIdx − 1):**

| | Code computes | Correct value |
|---|---|---|
| `walkDistE6` after step | `curBinDist − length(curBinIdx)` | `curBinDist − length(curBinIdx−1)` |
| `lowerX64` | price at `curBinDist − length(curBinIdx)` | price at `curBinDist − length(curBinIdx−1)` |
| `upperX64` | price at `curBinDist − length(curBinIdx) + length(curBinIdx−1)` | price at `curBinDist` |

The error equals `length(curBinIdx) − length(curBinIdx−1)` in E6 distance units and **accumulates** across every subsequent iteration.

**Contrast with `_fillAsks`**, which correctly advances by the current bin's own `lengthE6`: [4](#0-3) 

The asymmetry confirms the intended pattern: advance/retreat by the **current** bin's length, not the neighbour's.

---

### Impact Explanation

`getLiquidityDepth` is the primary on-chain depth API. Its `DepthLevel.binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` fields are the natural inputs for integrators computing `amountOutMinimum` before calling a router swap. [5](#0-4) 

When `length(curBinIdx) > length(curBinIdx−1)`, `walkDistE6` is shifted too far negative → bid prices are **understated** → integrators set `amountOutMinimum` below the true execution price → the swap executes at a worse rate than the integrator intended → direct loss of user principal. The error is proportional to the length difference and compounds across the depth ladder.

---

### Likelihood Explanation

Non-uniform `lengthE6` per bin is a first-class supported configuration: each bin carries its own `lengthE6` field set at pool creation. [6](#0-5) 

Any pool with at least two bid-side bins of different lengths triggers the bug. This is the normal case for pools that taper bin widths away from the mid price. The bug is deterministic and requires no special timing or privilege.

---

### Recommendation

Replace the `lenAbove` read-and-subtract with a subtraction of the **current** bin's `lengthE6` (already read two lines later). The upper edge of bin `b` equals the pre-subtraction `walkDistE6`; the lower edge equals `walkDistE6 − lengthE6(b)`:

```solidity
// Remove the lenAbove read entirely.
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
walkDistE6 -= int256(uint256(lengthE6));
uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
```

This mirrors the `_fillAsks` pattern exactly.

---

### Proof of Concept

```solidity
// Pool with curBinIdx=0, curBinDistFromProvidedPriceE6=0
// Bin  0: lengthE6 = 20_000  (2%)
// Bin -1: lengthE6 = 10_000  (1%)

// Correct geometry:
//   lower edge of bin -1 = 0 - 10_000 = -10_000
//   upper edge of bin -1 = 0

// _fillBids computes (first loop iteration, b = -1):
//   lenAbove = lengthE6(bin 0) = 20_000
//   walkDistE6 = 0 - 20_000 = -20_000   ← wrong (off by 10_000)
//   lowerX64 = price(-20_000)            ← wrong
//   upperX64 = price(-20_000 + 10_000)  = price(-10_000)  ← wrong (should be price(0))

// The entire price window [-20_000, -10_000] is shifted 10_000 E6 units below the
// true window [-10_000, 0], understating the bid price by ~1%.
// An integrator reading binAvgExecPriceX64 sets amountOutMinimum ~1% too low
// and accepts a swap that delivers ~1% less token1 than expected.
``` [7](#0-6)

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L39-45)
```text
  struct DepthLevel {
    int8 binIdx;
    uint256 amountInBin;
    uint256 amountCumulative;
    uint256 binAvgExecPriceX64;
    uint256 cumulativeAvgExecPriceX64;
  }
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L441-441)
```text
    ctx.cumDistE6 += int256(uint256(lengthE6));
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L456-456)
```text
    int256 walkDistE6 = int256(curBinDistFromProvidedPriceE6);
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L492-526)
```text
    for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
      // forge-lint: disable-next-line(unsafe-typecast)
      int8 binIdx = int8(b);
      // forge-lint: disable-next-line(unsafe-typecast)
      (,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
      // forge-lint: disable-next-line(unsafe-typecast)
      walkDistE6 -= int256(uint256(lenAbove));

      (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
      uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);

      uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 upperX64 =
        _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);

      uint256 amountScaled = uint256(t1);
      uint256 mStartX64 = upperX64;
      uint256 mEndX64 = lowerX64;
      uint256 amountExternal = _toExternal(amountScaled, token1ScaleMultiplier);

      (uint256 binAvg, uint256 newCumToken1Out, uint256 newCumToken0Sold) =
        _accumulateBidLevel(sellFeeX64, notionalFeeE8, amountExternal, mStartX64, mEndX64, cumToken1Out, cumToken0Sold);

      bids[out++] = DepthLevel({
        binIdx: binIdx,
        amountInBin: amountExternal,
        amountCumulative: newCumToken1Out,
        binAvgExecPriceX64: binAvg,
        cumulativeAvgExecPriceX64: _bidCumulativeAvgExecPriceX64(newCumToken1Out, newCumToken0Sold)
      });

      cumToken1Out = newCumToken1Out;
      cumToken0Sold = newCumToken0Sold;
    }
```

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L88-94)
```markdown
Each logical bin is **48 bits**:

- **bits 0–15:** `lengthE6` (uint16) — segment length in E6 distance units along the ladder.
- **bits 16–31:** `addFeeBuyE6` (uint16) — extra fee for the “buy token0” direction (E6).
- **bits 32–47:** `addFeeSellE6` (uint16) — extra fee for the “buy token1” direction (E6).

Up to **five** bins are packed per **`uint256`**, little-endian within the word (position `0` = lowest bits).
```
