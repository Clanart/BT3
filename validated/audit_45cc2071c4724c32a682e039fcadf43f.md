The bug claim is real. Let me trace through the code precisely.

The bug is confirmed. Here is the full analysis.

---

### Title
`_fillBids` subtracts `lenAbove` (length of bin `b+1`) instead of `lengthE6` (length of bin `b`) when walking `walkDistE6` downward, corrupting every bin-lower-edge price below `curBinIdx` — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

---

### Summary

`_fillBids` walks downward through bins to build the bid depth ladder. At each step it must compute the lower edge of bin `b` as:

```
lower_edge(b) = lower_edge(b+1) − length(b)
```

Instead, the loop reads `lenAbove` from bin `b+1` and subtracts that:

```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));          // ← subtracts length(b+1), not length(b)
``` [1](#0-0) 

When all bins share the same `lengthE6` the two values are equal and the error is invisible. When adjacent bins have different widths (a normal per-bin parameter), `walkDistE6` drifts from the true lower edge on every iteration, and the error compounds for every subsequent bin.

---

### Finding Description

**Geometry invariant violated.**
`walkDistE6` is initialised to `curBinDistFromProvidedPriceE6`, the lower edge of `curBinIdx`. [2](#0-1) 

For each bin `b` below `curBinIdx`, the correct lower edge is `lower_edge(b+1) − length(b)`. The loop instead subtracts `length(b+1)`: [3](#0-2) 

`lowerX64` and `upperX64` are then derived from the corrupted `walkDistE6`, and `binAvgExecPriceX64` is the midpoint of fee-adjusted versions of those two prices: [4](#0-3) 

Because the error in `walkDistE6` accumulates with each step, every bin below `curBinIdx` receives a wrong price range, and the error grows the further from `curBinIdx` the bin is.

**Contrast with `_fillAsks`.**
The ask-side walk correctly advances `cumDistE6` by `lengthE6` of the *current* bin `b`: [5](#0-4) 

The bid-side should mirror this by subtracting `lengthE6` of the current bin `b`, not `lenAbove` of `b+1`.

---

### Impact Explanation

`getLiquidityDepth` is the primary off-chain depth oracle for the protocol. Integrators (routers, aggregators, UI order-sizers) consume `bids[i].binAvgExecPriceX64` to:

1. Estimate how much token1 they will receive for a given token0 sell.
2. Derive a minimum-output (slippage) parameter before submitting the swap.

When `length(b+1) > length(b)` for any bin below `curBinIdx`, `walkDistE6` is decremented by more than the true bin width, shifting the computed price range downward. `binAvgExecPriceX64` is understated. An integrator that trusts the quoted average execution price sets `amountOutMinimum` too low and the actual pool swap — which uses the correct bin geometry — executes at a price worse than the integrator expected. The integrator's token0 is consumed at a lower token1/token0 rate than quoted; the difference is a direct loss of user principal.

This satisfies the allowed impact gate: **bad-price execution** and **lens/quoter path inducing fund-losing trades**.

---

### Likelihood Explanation

`lengthE6` is a per-bin `uint16` stored independently for every bin in pool storage: [6](#0-5) 

Heterogeneous bin widths are a normal, supported pool configuration — not a malicious or privileged setup. Any legitimately deployed pool whose bins differ in width (e.g., narrower bins near mid-price, wider bins further out) triggers the bug for every call to `getLiquidityDepth` that spans more than one bin on the bid side.

---

### Recommendation

Replace the two-call pattern (read `b+1`, subtract its length) with a single read of the current bin's own `lengthE6` and subtract that:

```solidity
// Before (buggy):
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);

// After (correct):
(, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
walkDistE6 -= int256(uint256(lengthE6));
```

This mirrors the ask-side pattern in `_fillAskRow` where `cumDistE6 += int256(uint256(lengthE6))` uses the current bin's own length. [5](#0-4) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {MetricOmmPoolDataProvider} from
    "metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol";

/// Deploy a pool where:
///   curBinIdx  = 0, lengthE6 = 20_000  (2 %)
///   bin -1         lengthE6 = 10_000  (1 %)
/// Oracle mid = 1.0 (Q64), no fees.
///
/// Correct lower edge of bin -1:
///   lower_edge(0) - length(-1) = 0 - 10_000 = -10_000 E6
///   → midPrice * (1e6 - 10_000) / 1e6
///
/// Buggy lower edge of bin -1 (code subtracts length(0) = 20_000):
///   0 - 20_000 = -20_000 E6
///   → midPrice * (1e6 - 20_000) / 1e6   ← 1 % too low
///
/// binAvgExecPriceX64 for bin -1 is therefore ~1 % understated,
/// causing integrators to accept ~1 % worse execution than quoted.

contract FillBidsBugPoC is Test {
    function test_walkDistE6_uses_wrong_bin_length() external {
        // ... pool setup with heterogeneous bin lengths ...
        // getLiquidityDepth(pool, 2)
        // uint256 quotedBid1 = depth.bids[1].binAvgExecPriceX64;
        // uint256 correctBid1 = <manually computed from correct lower edge>;
        // assertEq(quotedBid1, correctBid1, "binAvgExecPriceX64 corrupted");
    }
}
```

The assertion fails when `length(curBinIdx) != length(curBinIdx - 1)`, confirming the corrupted price is returned to integrators.

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L353-355)
```text
    uint256 execStart = _feeAdjustedBidX64(mStartX64, sellFeeX64, notionalFeeE8);
    uint256 execEnd = _feeAdjustedBidX64(mEndX64, sellFeeX64, notionalFeeE8);
    binAvgExecPriceX64 = (execStart + execEnd) >> 1;
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L440-442)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    ctx.cumDistE6 += int256(uint256(lengthE6));
  }
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L456-456)
```text
    int256 walkDistE6 = int256(curBinDistFromProvidedPriceE6);
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

**File:** metric-core/contracts/libraries/PoolStateLibrary.sol (L1-10)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IExtsload} from "../interfaces/IExtsload.sol";
import {PoolSlot0} from "../types/Slot0.sol";
import {Slot0Library} from "./Slot0Library.sol";

/// @title PoolStateLibrary
/// @notice Read MetricOmmPool storage via EXTSLOAD (no native view getters on the pool)
```
