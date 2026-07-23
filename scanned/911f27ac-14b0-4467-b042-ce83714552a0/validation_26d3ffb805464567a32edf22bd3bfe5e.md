The code is present and I have everything needed to make a determination. Let me trace the exact arithmetic.

**Pool's downward traversal** (`MetricOmmPool.sol` lines 1197–1200):
```solidity
curBinIdxCache--;
binState = _binStates[curBinIdxCache];          // load NEW (lower) bin
curBinDistE6Cache -= int24(uint24(binState.lengthE6)); // subtract NEW bin's length
```
After this, `curBinDistE6Cache` = lower boundary of the new lower bin.

**`_fillBids` downward traversal** (`MetricOmmPoolDataProvider.sol` lines 496–498):
```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // length of bin ABOVE
walkDistE6 -= int256(uint256(lenAbove));                                  // subtract ABOVE bin's length
```
Then `walkDistE6` is used as the lower boundary of bin `b`, and `walkDistE6 + lengthE6[b]` as the upper boundary.

**Concrete counterexample:**
- `curBinIdx = 0`, `curBinDistFromProvidedPriceE6 = 0`
- `lengthE6[0] = 10_000`, `lengthE6[-1] = 20_000`

Correct geometry: bin -1 spans `[-20_000, 0)`.

Pool traversal: `curBinDistE6Cache = 0 − 20_000 = −20_000` ✓

`_fillBids` for `b = −1`:
- `lenAbove = lengthE6[0] = 10_000`
- `walkDistE6 = 0 − 10_000 = −10_000` ✗ (should be −20_000)
- `lowerX64` = price(−10_000) ✗ (should be price(−20_000))
- `upperX64` = price(−10_000 + 20_000) = price(10_000) ✗ (should be price(0))

Both boundaries are wrong, and the error compounds for every subsequent bin below.

**Is heterogeneous `lengthE6` a normal configuration?** Yes. The factory's `_unpackAndValidateBinStates` accepts per-bin `lengthE6` values packed in `nonNegativeBinDataArray`/`negativeBinDataArray` with no requirement that they be equal. Any pool creator can configure this.

**Does this meet the lens/quoter impact gate?** The Smart Audit Pivots state: *"quoted amounts, path decoding, state reads, and simulated reverts must match actual pool execution closely enough that integrations are not induced into fund-losing trades."* Here the depth ladder's `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` for every bid bin below `curBinIdx` are computed from wrong price bounds whenever adjacent bins differ in length. An integrator using `getLiquidityDepth` to size a sell-token0 swap would receive a predicted execution price that diverges from the actual pool price, potentially executing at a materially worse price.

---

### Title
`_fillBids` subtracts the wrong bin's `lengthE6` when walking downward, corrupting `lowerX64`/`upperX64` for all bid bins below the active bin in heterogeneous-length pools — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

### Summary
`_fillBids` computes `walkDistE6` by subtracting `lenAbove` (the length of bin `b+1`) instead of the length of bin `b` itself. This is the inverse of how `MetricOmmPool` traverses bins downward, which subtracts the **new** (lower) bin's length. The mismatch produces wrong `lowerX64`/`upperX64` for every bid bin below `curBinIdx` whenever adjacent bins have different `lengthE6` values.

### Finding Description
In `_fillBids`, the loop initialises `walkDistE6 = curBinDistFromProvidedPriceE6` (the lower boundary of `curBinIdx`) and then for each bin `b < curBinIdx`:

```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
``` [1](#0-0) 

`walkDistE6` is then used as the lower boundary of bin `b`, and `walkDistE6 + lengthE6[b]` as the upper boundary. [2](#0-1) 

The pool itself, when traversing downward, subtracts the **new** (lower) bin's length:
```solidity
curBinIdxCache--;
binState = _binStates[curBinIdxCache];
curBinDistE6Cache -= int24(uint24(binState.lengthE6));
``` [3](#0-2) 

The lens subtracts `lengthE6[b+1]` (the bin above) but the pool subtracts `lengthE6[b]` (the bin being entered). These are equal only when all bins share the same length. For any pool with heterogeneous bin lengths — a fully supported factory configuration — the computed `lowerX64` and `upperX64` diverge from the actual bin boundaries, and the error accumulates with each step further from `curBinIdx`.

The factory explicitly supports per-bin `lengthE6` values: [4](#0-3) 

### Impact Explanation
`getLiquidityDepth` returns a `bids` array whose `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` fields are computed from wrong price bounds for every bin below `curBinIdx`. An integrator using this depth ladder to decide whether and how much to sell token0 receives a predicted execution price that does not match what the pool will actually execute. The integrator may execute a sell swap expecting a price that the pool will not deliver, resulting in a direct loss of value to the integrator above Sherlock Medium thresholds when the bin-length difference is material (e.g., a 2× difference in adjacent bin lengths shifts the reported price range by the full width of one bin).

### Likelihood Explanation
Any pool with at least two adjacent bid bins of different `lengthE6` triggers the bug. The factory imposes no constraint requiring equal bin lengths; heterogeneous lengths are the expected use case for pools that want tighter pricing near the mid and wider bins further out. The bug is triggered by a normal `getLiquidityDepth` call with no privileged setup.

### Recommendation
Replace the `lenAbove` read with the current bin's own `lengthE6`, which is already fetched on the next line:

```solidity
// Before (wrong):
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);

// After (correct):
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
walkDistE6 -= int256(uint256(lengthE6));
```

This matches the pool's own downward traversal, which subtracts the new (lower) bin's length.

### Proof of Concept
1. Deploy a pool with `curBinDistFromProvidedPriceE6 = 0`, bin 0 `lengthE6 = 10_000`, bin −1 `lengthE6 = 20_000`, both with token1 liquidity.
2. Call `getLiquidityDepth(pool, 2)`.
3. The returned `bids[1]` (bin −1) will have `lowerX64 = price(−10_000)` and `upperX64 = price(10_000)`.
4. Manually compute: correct lower = `price(−20_000)`, correct upper = `price(0)`.
5. Assert `bids[1].binAvgExecPriceX64` matches neither the correct average nor the actual pool execution price for a sell swap that exhausts bin −1.

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L496-498)
```text
      (,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
      // forge-lint: disable-next-line(unsafe-typecast)
      walkDistE6 -= int256(uint256(lenAbove));
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L503-506)
```text
      uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 upperX64 =
        _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1197-1200)
```text
          curBinIdxCache--;
          binState = _binStates[curBinIdxCache];
          curPosInBinCache = type(uint104).max;
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L628-630)
```text
          nonNegativeBinStates[k] = BinState({
            token0BalanceScaled: 0, token1BalanceScaled: 0, lengthE6: length, addFeeBuyE6: buyFee, addFeeSellE6: sellFee
          });
```
