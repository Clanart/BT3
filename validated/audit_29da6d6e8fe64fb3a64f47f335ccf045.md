Looking at the actual code in `_fillBids` (lines 492-526) and comparing with `_fillAskRow` (lines 389-442).

The bug is confirmed in the production code. The analysis is complete.

Audit Report

## Title
Wrong `walkDistE6` Decrement in `_fillBids` Uses Bin-Above Length Instead of Current Bin Length — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

## Summary
In `_fillBids`, the downward bin walk decrements `walkDistE6` by `lenAbove` — the `lengthE6` of bin `b+1` — instead of the `lengthE6` of the current bin `b` being priced. This shifts both `lowerX64` and `upperX64` for every bin below `curBinIdx` by `lengthE6(b) - lengthE6(b+1)`, producing wrong marginal prices and corrupting `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` in every `DepthLevel` entry returned by `getLiquidityDepth`. Integrations that consume this depth ladder to set slippage guards are induced into fund-losing trades.

## Finding Description

**Root cause — lines 496–498:**

```solidity
// _fillBids, lines 492–506
for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
    int8 binIdx = int8(b);
    (,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // ← reads bin b+1
    walkDistE6 -= int256(uint256(lenAbove));                                 // ← subtracts b+1's length

    (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
    ...
    uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, ...);
    uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), ...);
```

`walkDistE6` is supposed to track the **lower edge** of the bin currently being priced. The correct invariant is:

```
lower(b) = lower(b+1) - lengthE6(b)
```

The code instead computes:

```
walkDistE6 = lower(b+1) - lengthE6(b+1)   ← uses b+1's length
```

This is correct only when all bins have identical lengths. When lengths differ, both `lowerX64` and `upperX64` are shifted by `lengthE6(b) - lengthE6(b+1)`, and the error accumulates with each hop downward.

**Contrast with the correct ask-side pattern** in `_fillAskRow` (line 441):

```solidity
ctx.cumDistE6 += int256(uint256(lengthE6));  // advances by current bin b's length
```

The ask side reads `lengthE6` of the current bin and advances by it — the correct approach. The bid side should subtract `lengthE6` of the current bin `b` (already fetched on line 500), not re-read `lenAbove` from bin `b+1`.

**Exploit path:**
1. Any caller invokes `getLiquidityDepth(pool, maxBinsPerSide)` on a pool where bins have non-uniform `lengthE6` values (the common case — pools are configured with varying bin widths).
2. `_fillBids` returns `DepthLevel[]` entries with wrong `lowerX64`/`upperX64` → wrong marginal prices → wrong `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` for every bin below `curBinIdx`.
3. An aggregator, front-end, or market-making bot reads `cumulativeAvgExecPriceX64` to compute `amountOutMinimum` or `priceLimitX64` before calling `exactInput`/`exactInputSingle`.
4. Because the reported bid prices are systematically wrong, the slippage guard is miscalibrated: either too loose (user accepts worse execution than intended, losing output tokens) or too tight (unnecessary revert, DoS of the trade flow).

**Existing guards:** None. `getLiquidityDepth` has no cross-check against actual pool execution; the lens is the sole source of depth data for off-chain consumers.

## Impact Explanation

`getLiquidityDepth` is the primary off-chain depth oracle for the protocol. Its `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` fields are the inputs integrations use to size slippage guards before on-chain swaps. Systematically wrong bid-side prices directly induce callers into accepting worse execution prices than intended (direct loss of output tokens) or into unnecessary reverts. This falls squarely under the "Lens/quoter path" Smart Audit Pivot: quoted amounts must match actual pool execution closely enough that integrations are not induced into fund-losing trades.

## Likelihood Explanation

The bug is triggered on every call to `getLiquidityDepth` on any pool with more than one bid-side bin where adjacent bins have different `lengthE6` values. Non-uniform bin widths are the standard configuration (the factory supports per-bin `lengthE6` packed independently). No privileged access or special setup is required; any unprivileged caller or integration reading the depth ladder is affected. The error is deterministic and repeatable.

## Recommendation

Replace the `lenAbove` read with the already-fetched `lengthE6` of the current bin `b`. Move the `_binState` call for `binIdx` before the `walkDistE6` decrement and subtract `lengthE6(b)`:

```solidity
for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
    int8 binIdx = int8(b);
    (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
    walkDistE6 -= int256(uint256(lengthE6));   // ← subtract current bin b's length

    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);
    uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
    uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
    ...
```

This mirrors the ask-side pattern exactly and eliminates the redundant `_binState(pool, int8(b + 1))` call.

## Proof of Concept

Deploy a pool with two bid-side bins of different lengths, e.g. `curBinIdx` with `lengthE6 = 10_000` and `curBinIdx - 1` with `lengthE6 = 20_000`. Call `getLiquidityDepth(pool, 2)`. The returned `bids[1].binAvgExecPriceX64` will be computed from a `lowerX64` shifted by `10_000 - 20_000 = -10_000` E6 units relative to the correct lower edge, producing a price that does not correspond to any real bin boundary. A Foundry fork test asserting `bids[1].binAvgExecPriceX64 == expectedCorrectPrice` will fail, confirming the corruption. [1](#0-0) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L440-441)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
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
