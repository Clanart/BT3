Audit Report

## Title
`_fillBids` subtracts the wrong bin's `lengthE6` when walking downward, corrupting bid depth ladder prices for all non-current bins in pools with non-uniform bin widths — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

## Summary
`MetricOmmPoolDataProvider._fillBids` computes `walkDistE6` (the lower-edge distance of each bid bin) by subtracting `lenAbove` — the `lengthE6` of bin `b+1` (the bin above) — instead of `lengthE6` of bin `b` (the bin being priced). In any pool where adjacent bins have different widths, every `lowerX64`/`upperX64` pair below `curBinIdx` is wrong, and the error accumulates with each step down the ladder, causing `getLiquidityDepth` to return corrupted bid prices that can induce fund-losing trades in integrations.

## Finding Description
`_fillBids` initialises `walkDistE6 = curBinDistFromProvidedPriceE6` (the lower edge of `curBinIdx`). For each step `b = curBinIdx−1, curBinIdx−2, …`:

```solidity
// metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol L496-498
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // reads b+1
walkDistE6 -= int256(uint256(lenAbove));                                 // subtracts b+1's length
``` [1](#0-0) 

The correct step is `walkDistE6 -= lengthE6(b)`. The current bin's `lengthE6` is already fetched on the very next line but is only used for the `upperX64` offset:

```solidity
// L500-506
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx); // bin b
uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, ...);
uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), ...);
``` [2](#0-1) 

**Contrast with `_fillAsks` (correct):** `_fillAskRow` fetches `lengthE6` from the current bin `binIdx = int8(b)` and advances `ctx.cumDistE6 += int256(uint256(lengthE6))` — always the current bin's own length. [3](#0-2) [4](#0-3) 

**Confirmed by pool's own swap logic:** `MetricOmmPool` walks downward by subtracting the *current* bin's length after decrementing `curBinIdxCache` — `curBinDistE6Cache -= int24(uint24(binState.lengthE6))` where `binState = _binStates[curBinIdxCache]`. [5](#0-4) 

The error is `lengthE6(b+1) − lengthE6(b)` per step and accumulates for every subsequent bin below `curBinIdx`. The resulting `lowerX64`/`upperX64` are wrong, so `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` are wrong for every bid bin below the current one.

## Impact Explanation
`getLiquidityDepth` is the primary off-chain depth/quoter surface for integrators, UIs, and routing engines sizing sell trades (token0 → token1). When the bid ladder reports incorrect execution prices due to wrong bin edge distances, a seller sizes a trade expecting `X` token1 but the pool — which uses the correct geometry — delivers a different amount. The seller suffers a direct, quantifiable shortfall (or surplus) on every sell routed through the corrupted ladder. The magnitude scales with the width differential between adjacent bins and the depth of the walk. This falls squarely within the lens/quoter scope gate: *quoted amounts must match actual pool execution closely enough that integrations are not induced into fund-losing trades.*

## Likelihood Explanation
Pool creation via `MetricOmmPoolFactory.createPool` is permissionless. [6](#0-5) 

Each bin's `lengthE6` is an independent `uint16` with no uniformity constraint enforced by the factory — bins are unpacked and stored individually. [7](#0-6) 

Any pool — including all existing production pools — with bins of different widths triggers the bug on every `getLiquidityDepth` call that spans more than one bid bin. Non-uniform bin widths (e.g., wider bins near the spread, narrower bins deep in the book) are a standard, expected pool topology requiring no malicious setup.

## Recommendation
Hoist the `lengthE6` read for bin `b` above the `walkDistE6` update and use it instead of the separate `lenAbove` lookup, mirroring `_fillAsks`:

```solidity
// Replace:
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);

// With:
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
walkDistE6 -= int256(uint256(lengthE6)); // current bin b, not b+1
```

This eliminates the redundant `lenAbove` read and correctly subtracts the current bin's own length, matching both `_fillAsks` and the pool's swap logic.

## Proof of Concept
Deploy a pool with `curBinIdx = 0` (`lengthE6 = 20_000`), bin −1 (`lengthE6 = 5_000`), bin −2 (`lengthE6 = 10_000`). Set `curBinDistFromProvidedPriceE6 = 0`.

**Expected `walkDistE6`:**
- Bin −1: `0 − 5_000 = −5_000`
- Bin −2: `−5_000 − 10_000 = −15_000`

**Actual `walkDistE6` (buggy):**
- Bin −1: `lenAbove = lengthE6(bin 0) = 20_000` → `walkDistE6 = 0 − 20_000 = −20_000` ✗
- Bin −2: `lenAbove = lengthE6(bin −1) = 5_000` → `walkDistE6 = −20_000 − 5_000 = −25_000` ✗

Call `getLiquidityDepth(pool, 3)`. Assert `bids[1].binAvgExecPriceX64` diverges from the price computed by `simulateSwapAndRevert` for the same cumulative token1 amount. Then execute a sell sized to consume bin −1 using the ladder's quoted price; observe the actual token1 received differs from the ladder's implied amount, confirming the fund-losing trade path.

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L403-403)
```text
    (uint104 t0,, uint16 lengthE6, uint16 addFeeBuyE6,) = PoolStateLibrary._binState(pool, binIdx);
```

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

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L500-506)
```text
      (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
      uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);

      uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 upperX64 =
        _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1198-1200)
```text
          binState = _binStates[curBinIdxCache];
          curPosInBinCache = type(uint104).max;
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L156-156)
```text
  function createPool(PoolParameters calldata params) external override returns (address pool) {
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L626-630)
```text
          (uint16 length, uint16 buyFee, uint16 sellFee) = binData.unpack();
          if (length == 0) break;
          nonNegativeBinStates[k] = BinState({
            token0BalanceScaled: 0, token1BalanceScaled: 0, lengthE6: length, addFeeBuyE6: buyFee, addFeeSellE6: sellFee
          });
```
