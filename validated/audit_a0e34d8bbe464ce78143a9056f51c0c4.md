Now I have the full `_fillBids` code. Let me verify the `_fillAsks` accumulation pattern to confirm the asymmetry.

The bug is real and traceable. Here is the full analysis:

---

### Title
`_fillBids` subtracts the wrong bin's `lengthE6` when stepping the distance cursor, producing wrong `lowerX64`/`upperX64` price bounds for every bid depth level below `curBinIdx` — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

### Summary

`_fillBids` walks downward from `curBinIdx` to `lowCap`. For each bin `b`, it must set `walkDistE6` to the lower boundary of that bin. It does so by subtracting `lenAbove`, which it reads from bin `b+1`. But the correct step is to subtract the length of bin `b` itself, not bin `b+1`. When bins have non-uniform `lengthE6` (the normal case), every bid depth level below `curBinIdx` receives wrong `lowerX64`/`upperX64` price bounds, corrupting `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64`.

### Finding Description

**Correct geometry:**

`curBinDistFromProvidedPriceE6` is the lower boundary of `curBinIdx`. The lower boundary of bin `b` is:

```
curBinDistFromProvidedPriceE6 - len(curBinIdx-1) - len(curBinIdx-2) - ... - len(b)
```

To step from the lower boundary of bin `b+1` to the lower boundary of bin `b`, the code must subtract `len(b)`.

**What the code actually does:** [1](#0-0) 

```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
```

At iteration `b = curBinIdx - 1`, `b+1 = curBinIdx`, so `lenAbove = len(curBinIdx)`. The code subtracts the length of the bin **above** the current target, not the length of the current target bin. The error is `len(curBinIdx) - len(curBinIdx-1)` for the first step, and it accumulates differently for every subsequent step.

**Contrast with `_fillAsks`**, which correctly accumulates the current bin's own `lengthE6` at the end of each iteration: [2](#0-1) 

```solidity
ctx.cumDistE6 += int256(uint256(lengthE6));  // lengthE6 of the bin just processed
```

**Concrete example:**

| Bin | `lengthE6` | Correct lower boundary | Code's `walkDistE6` | Error |
|-----|-----------|----------------------|---------------------|-------|
| `curBinIdx` (=2) | 100 | 500 | 500 | 0 |
| 1 | 200 | 300 | 500 − 100 = **400** | +100 |
| 0 | 300 | 0 | 400 − 200 = **200** | +200 |

Both `lowerX64` and `upperX64` for bins 1 and 0 are shifted upward, making the reported bid prices higher than the actual pool prices. An integrator using these values to set `amountOutMinimum` for a sell will set it too high (if they notice) or too low (if they scale from the wrong reference), potentially executing a loss-making sell.

### Impact Explanation

`getLiquidityDepth` is the primary public lens for integrators to read bid depth and derive slippage parameters. The `binAvgExecPriceX64` field in every `DepthLevel` below `curBinIdx` is wrong whenever adjacent bins have different `lengthE6` values — the normal, expected configuration per the factory's bin packing format. [3](#0-2) 

The error magnitude is bounded by the difference between adjacent bin lengths (up to the full `uint16` range, i.e., up to 65535 E6 units out of a ±999,999 E6 total range). This is large enough to systematically misreport bid prices, inducing integrators to set `amountOutMinimum` from a stale/wrong price and execute loss-making sells above Sherlock Medium thresholds.

### Likelihood Explanation

Any pool with non-uniform bin lengths (the standard configuration) triggers this bug on every call to `getLiquidityDepth` when `curBinIdx` is not at the lowest bin. No privileged action is required — any caller of the public `getLiquidityDepth` function observes the corrupted output. [4](#0-3) 

### Recommendation

Replace the `lenAbove` read with the current bin's own `lengthE6`, read after fetching the bin state for `binIdx`:

```solidity
// After: (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
walkDistE6 -= int256(uint256(lengthE6));  // subtract current bin's length, not the bin above
```

This mirrors the correct pattern used in `_fillAsks` (line 441).

### Proof of Concept

1. Deploy a pool with bins: `curBinIdx=2` (`lengthE6=100`), bin 1 (`lengthE6=200`), bin 0 (`lengthE6=300`). Set `curBinDistFromProvidedPriceE6 = 500`.
2. Call `getLiquidityDepth(pool, 3)`.
3. Observe `bids[1]` (bin 1): `lowerX64 = price(400)` instead of correct `price(300)`; `upperX64 = price(600)` instead of correct `price(500)`.
4. Observe `bids[2]` (bin 0): `lowerX64 = price(200)` instead of correct `price(0)`; `upperX64 = price(500)` instead of correct `price(300)`.
5. Assert `binAvgExecPriceX64` for both bins is systematically higher than the true pool bid price.
6. Show that an integrator using `bids[1].binAvgExecPriceX64` to set `amountOutMinimum` for a sell executes at a worse price than expected. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L38-45)
```text
  /// @notice One depth step on the ask (buy token0) or bid (sell token0) side.
  struct DepthLevel {
    int8 binIdx;
    uint256 amountInBin;
    uint256 amountCumulative;
    uint256 binAvgExecPriceX64;
    uint256 cumulativeAvgExecPriceX64;
  }
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L125-126)
```text
  function getLiquidityDepth(address pool, uint8 maxBinsPerSide) external returns (LiquidityDepth memory depth) {
    if (maxBinsPerSide == 0 || maxBinsPerSide > MAX_BINS_PER_SIDE_CAP) revert MaxBinsPerSideTooLarge();
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L441-441)
```text
    ctx.cumDistE6 += int256(uint256(lengthE6));
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L492-506)
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
```
