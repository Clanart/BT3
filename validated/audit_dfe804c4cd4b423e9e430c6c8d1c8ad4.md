Now I have all the code I need. Let me analyze the exact bug.

The bug is real and demonstrable. Here is the full analysis:

---

### Title
`_fillBids` subtracts the length of bin `b+1` instead of bin `b` when walking `walkDistE6` downward, producing wrong `lowerX64`/`upperX64` for every bid bin below `curBinIdx` when bins have unequal lengths — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

### Summary

`_fillBids` computes the lower-edge distance (`walkDistE6`) for each bid bin by subtracting the length of the bin **above** (`b+1`) rather than the length of the bin **being entered** (`b`). When adjacent bins have different `lengthE6` values — a normal, factory-permitted configuration — every bin below `curBinIdx` gets a wrong price range, corrupting `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` in the returned `DepthLevel` array.

### Finding Description

In `_fillBids`, the loop that walks below `curBinIdx` reads:

```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
``` [1](#0-0) 

`lenAbove` is the `lengthE6` of bin `b+1` (the bin just exited), not of bin `b` (the bin being entered). The correct lower edge of bin `b` is:

```
lower_edge(b) = lower_edge(b+1) - length(b)
```

The code computes instead:

```
lower_edge(b) = lower_edge(b+1) - length(b+1)
```

This is only accidentally correct when all bins share the same length. The factory imposes no such constraint — each bin carries its own `lengthE6` field set independently at pool creation: [2](#0-1) 

The actual pool swap engine does it correctly: after decrementing `curBinIdxCache`, it subtracts the `lengthE6` of the **newly entered** bin: [3](#0-2) 

The ask-side lens (`_fillAskRow`) also does it correctly — it advances `ctx.cumDistE6` by the length of the **current** bin `b` after processing it: [4](#0-3) 

Because `walkDistE6` is wrong, both `lowerX64` and `upperX64` passed to `_accumulateBidLevel` are wrong for every bin below `curBinIdx`: [5](#0-4) 

This corrupts `mStartX64`/`mEndX64`, and therefore `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` in the returned `bids` array.

### Impact Explanation

`getLiquidityDepth` is the primary off-chain read path for bid-side depth. Integrators (aggregators, market-makers, UIs) read `bids[i].binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` to decide whether to submit a sell order and at what `priceLimitX64`. When bins have unequal lengths, the reported bid prices diverge from the prices the pool actually executes at. A seller who reads an inflated bid price and submits a sell order with a matching `priceLimitX64` will either:

- receive less token1 than expected (the pool executes at the true, lower price), or
- have the swap revert if the true execution price falls below their limit.

Both outcomes represent direct loss of expected proceeds to the seller. This is the "bad-price execution" impact explicitly listed in the contest gate: *"stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

### Likelihood Explanation

Any pool whose bins were configured with non-uniform `lengthE6` values triggers the bug. The factory permits this freely — it only validates that cumulative distances stay within `(-1e6, 1e6)`, not that all bins are equal length. A pool with, e.g., a narrow current bin and wider outer bins (a common liquidity-concentration pattern) will exhibit the error on every call to `getLiquidityDepth`.

### Recommendation

Replace the `lenAbove` read with the length of the current bin `b`, which is already fetched two lines later. The corrected loop body:

```solidity
for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
    int8 binIdx = int8(b);
    (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) =
        PoolStateLibrary._binState(pool, binIdx);

    walkDistE6 -= int256(uint256(lengthE6));   // ← subtract length of bin b, not b+1

    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);
    uint256 lowerX64   = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
    uint256 upperX64   = _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
    // ... rest unchanged
}
```

This mirrors the pattern used by `_fillAskRow` (accumulate by the current bin's own length) and matches the pool's own swap engine.

### Proof of Concept

1. Deploy a pool with three bins: `curBinIdx = 0` with `lengthE6 = 10_000`, bin `-1` with `lengthE6 = 20_000`, bin `-2` with `lengthE6 = 5_000`.
2. Call `getLiquidityDepth(pool, 3)`.
3. For bin `-1` the code subtracts `length(0) = 10_000` → `walkDistE6 = curBinDist - 10_000`. The correct lower edge is `curBinDist - 20_000`. The reported `lowerX64`/`upperX64` are shifted by `+10_000 E6` units, inflating the reported bid price.
4. For bin `-2` the error compounds: the code subtracts `length(-1) = 20_000` → `walkDistE6 = curBinDist - 30_000`. The correct lower edge is `curBinDist - 25_000`. The reported price is now shifted by `+5_000 E6` units.
5. Manually compute expected prices from raw bin state and compare against `bids[1].binAvgExecPriceX64` and `bids[2].binAvgExecPriceX64` — they will not match.
6. Submit a sell order using the inflated `cumulativeAvgExecPriceX64` as `priceLimitX64`; the swap executes at the true (lower) price, delivering less token1 than the integrator expected.

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L440-441)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    ctx.cumDistE6 += int256(uint256(lengthE6));
```

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L580-591)
```text
        BinDataLibrary.BinData binData = BinDataLibrary.toBinData(packed, j);
        (uint256 length,,) = binData.unpack();
        if (length == 0) {
          if (j == 0) revert BinLengthZero(posBinCount);
          break;
        }

        cumulativeDistance += length.toInt256();
        if (cumulativeDistance >= 1e6) {
          revert BinDistanceOutOfRange(posBinCount, cumulativeDistance);
        }
        posBinCount++;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1107-1110)
```text
          curBinIdxCache--;
          binState = _binStates[curBinIdxCache];
          curPosInBinCache = type(uint104).max;
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```
