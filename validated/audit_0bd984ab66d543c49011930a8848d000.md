The code is confirmed. Let me verify the exact logic in `_fillBids` vs `_fillAsks` to validate the claim.

Audit Report

## Title
`_fillBids` subtracts the above bin's `lengthE6` instead of the current bin's, producing a shifted price window for every sub-current bid bin — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

## Summary

In `_fillBids`, the loop that walks below `curBinIdx` reads `lenAbove` from bin `b+1` and subtracts it from `walkDistE6` to advance the cursor, then separately reads `lengthE6` from bin `b` to compute the upper edge. The correct step is to subtract the **current** bin `b`'s own `lengthE6`. For any pool with non-uniform bin lengths this produces a shifted `[lowerX64, upperX64]` price window for every bin below `curBinIdx`, making every `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` in the bid ladder wrong. Integrators using these values to set `amountOutMinimum` will accept swaps at worse rates than intended, causing direct loss of user principal.

## Finding Description

`walkDistE6` is initialised to `curBinDistFromProvidedPriceE6`, which is the **lower edge** of `curBinIdx` (confirmed by the current-bin block at lines 466–469, which uses `walkDistE6` as the lower edge and `walkDistE6 + lengthE6(curBinIdx)` as the upper edge without modifying `walkDistE6` afterward). [1](#0-0) 

For each lower bin `b`, the correct geometry is:
- upper edge of `b` = lower edge of `b+1` = pre-step `walkDistE6`
- lower edge of `b` = `walkDistE6 − lengthE6(b)`

But the loop reads `lenAbove = lengthE6(b+1)` and subtracts that: [2](#0-1) 

It then uses the post-subtraction `walkDistE6` as the lower edge and `walkDistE6 + lengthE6(b)` as the upper edge: [3](#0-2) 

**Concrete arithmetic (first loop iteration, `b = curBinIdx − 1`):**

| | Code computes | Correct value |
|---|---|---|
| `walkDistE6` after step | `curBinDist − length(curBinIdx)` | `curBinDist − length(curBinIdx−1)` |
| `lowerX64` | `price(curBinDist − length(curBinIdx))` | `price(curBinDist − length(curBinIdx−1))` |
| `upperX64` | `price(curBinDist − length(curBinIdx) + length(curBinIdx−1))` | `price(curBinDist)` |

The error equals `length(curBinIdx) − length(curBinIdx−1)` in E6 distance units and **accumulates** across every subsequent iteration because `walkDistE6` carries the wrong value into the next step.

By contrast, `_fillAsks` correctly advances by the **current** bin's own `lengthE6`: [4](#0-3) [5](#0-4) 

The asymmetry confirms the intended pattern: advance/retreat by the **current** bin's length, not the neighbour's.

## Impact Explanation

`getLiquidityDepth` is the primary on-chain depth API. Its `DepthLevel.binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` fields are the natural inputs for integrators computing `amountOutMinimum` before calling a router swap. [6](#0-5) 

When `length(curBinIdx) > length(curBinIdx−1)`, `walkDistE6` is shifted too far negative → bid prices are **understated** → integrators set `amountOutMinimum` below the true execution price → the swap executes at a worse rate than the integrator intended → direct loss of user principal. The error is proportional to the length difference and compounds across the depth ladder. This satisfies the lens/quoter path criterion: quoted amounts must match actual pool execution closely enough that integrations are not induced into fund-losing trades.

## Likelihood Explanation

Non-uniform `lengthE6` per bin is a first-class supported configuration — each bin carries its own `lengthE6` field set at pool creation, and the factory documentation explicitly describes tapering bin widths away from the mid price as a normal use case. [7](#0-6) 

Any pool with at least two bid-side bins of different lengths triggers the bug. No special timing, privilege, or attacker action is required — a read-only call to `getLiquidityDepth` on any such pool returns corrupted data. The bug is deterministic and repeatable.

## Recommendation

Remove the `lenAbove` read entirely. Capture the upper edge before subtracting, then subtract the **current** bin's `lengthE6`:

```solidity
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);

uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
walkDistE6 -= int256(uint256(lengthE6));
uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
```

This mirrors the `_fillAsks` / `_fillAskRow` pattern exactly and eliminates the extra `_binState` call.

## Proof of Concept

```solidity
// Pool: curBinIdx=0, curBinDistFromProvidedPriceE6=0
// Bin  0: lengthE6 = 20_000  (2%)
// Bin -1: lengthE6 = 10_000  (1%)

// Correct geometry for bin -1:
//   upper edge = 0  (lower edge of bin 0)
//   lower edge = 0 - 10_000 = -10_000

// _fillBids computes (first loop iteration, b = -1):
//   lenAbove = lengthE6(bin 0) = 20_000
//   walkDistE6 = 0 - 20_000 = -20_000          ← wrong (off by 10_000)
//   lowerX64 = price(-20_000)                   ← wrong
//   upperX64 = price(-20_000 + 10_000) = price(-10_000)  ← wrong (should be price(0))
//
// Entire price window [-20_000, -10_000] is shifted 10_000 E6 units below
// the true window [-10_000, 0], understating the bid price by ~1%.
// An integrator reading binAvgExecPriceX64 sets amountOutMinimum ~1% too low
// and accepts a swap delivering ~1% less token1 than expected.
```

A Foundry test can deploy a pool with two bid-side bins of different lengths (e.g., `lengthE6 = [20_000, 10_000]`), call `getLiquidityDepth`, and assert that `bids[1].binAvgExecPriceX64` matches the value produced by `simulateSwapAndRevert` for the corresponding token1 amount — the assertion will fail with the current code and pass after the fix.

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

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L406-409)
```text
    uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, ctx.cumDistE6, Math.Rounding.Floor);
    // forge-lint: disable-next-line(unsafe-typecast)
    uint256 upperX64 =
      _priceFromMidAndDistE6(midPriceX64, ctx.cumDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L441-441)
```text
    ctx.cumDistE6 += int256(uint256(lengthE6));
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L456-456)
```text
    int256 walkDistE6 = int256(curBinDistFromProvidedPriceE6);
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

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L88-94)
```markdown
Each logical bin is **48 bits**:

- **bits 0–15:** `lengthE6` (uint16) — segment length in E6 distance units along the ladder.
- **bits 16–31:** `addFeeBuyE6` (uint16) — extra fee for the “buy token0” direction (E6).
- **bits 32–47:** `addFeeSellE6` (uint16) — extra fee for the “buy token1” direction (E6).

Up to **five** bins are packed per **`uint256`**, little-endian within the word (position `0` = lowest bits).
```
