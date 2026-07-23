The bug is real and confirmed. Let me trace through the exact mechanics before rendering a verdict.

**The `_fillBids` downward walk (lines 492–498):**

```solidity
for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
    (,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // reads length of bin b+1
    walkDistE6 -= int256(uint256(lenAbove));                                 // subtracts length of bin b+1
    (, uint104 t1, uint16 lengthE6,,) = PoolStateLibrary._binState(pool, binIdx); // reads length of bin b
    uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, ...);
    uint256 upperX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), ...);
```

**Correct invariant:** `lowerEdge(b) = lowerEdge(b+1) − lengthE6(b)`. To step from the lower edge of `b+1` to the lower edge of `b`, you subtract `lengthE6(b)` — the current bin's own length.

**What the code does:** It subtracts `lenAbove = lengthE6(b+1)` — the length of the bin *above* — then uses `lengthE6(b)` only for the `upperX64` offset. This is only correct when all bins have the same length.

**Confirmed by comparison with `_fillAsks` (correct):** [1](#0-0) 

`_fillAsks` advances `ctx.cumDistE6 += int256(uint256(lengthE6))` using the *current* bin's length. `_fillBids` should mirror this by subtracting the *current* bin's length, but instead subtracts the bin above's length.

**Confirmed by the pool's own swap logic (correct):** [2](#0-1) 

`curBinDistE6Cache -= int24(uint24(binState.lengthE6))` — the pool subtracts the *current* bin's length when walking downward. The lens does not.

**Non-uniform bin lengths are a normal, permissionless pool configuration.** The factory's `_unpackAndValidateBinStates` processes each bin's `lengthE6` independently with no uniformity constraint: [3](#0-2) 

`createPool` is permissionless once `poolDeployer` is set: [4](#0-3) [5](#0-4) 

No malicious setup is required — any legitimately deployed pool with bins of different widths triggers the bug.

---

### Title
`_fillBids` subtracts the wrong bin's `lengthE6` when walking downward, corrupting bid depth ladder prices for all non-current bins in pools with non-uniform bin widths — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

### Summary
`MetricOmmPoolDataProvider._fillBids` computes the lower-edge distance (`walkDistE6`) for each bid bin by subtracting `lenAbove` — the `lengthE6` of bin `b+1` (the bin above) — instead of `lengthE6` of bin `b` (the bin being priced). In any pool where adjacent bins have different widths, every `lowerX64`/`upperX64` pair below `curBinIdx` is wrong, and the error accumulates with each step down the ladder.

### Finding Description
`_fillBids` initialises `walkDistE6 = curBinDistFromProvidedPriceE6` (the lower edge of `curBinIdx`). For each step `b = curBinIdx−1, curBinIdx−2, …`:

```solidity
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // BUG: reads b+1
walkDistE6 -= int256(uint256(lenAbove));                                 // BUG: subtracts b+1's length
```

The correct step is `walkDistE6 -= lengthE6(b)`. The code instead subtracts `lengthE6(b+1)`. For iteration `b = curBinIdx−1`:

| | Computed | Correct |
|---|---|---|
| `walkDistE6` | `curBinDist − lengthE6(curBinIdx)` | `curBinDist − lengthE6(curBinIdx−1)` |

The error is `lengthE6(curBinIdx) − lengthE6(curBinIdx−1)` and accumulates for every subsequent bin. The resulting `lowerX64`/`upperX64` are wrong, so `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` are wrong for every bid bin below the current one. [6](#0-5) 

### Impact Explanation
`getLiquidityDepth` is the primary off-chain depth/quoter surface for integrators, UIs, and routing engines sizing sell trades (token0 → token1). When the bid ladder reports inflated execution prices (bins above are wider than bins below), a seller sizes a trade expecting `X` token1 but the pool — which uses the correct geometry — delivers less. The seller suffers a direct, quantifiable shortfall on every sell routed through the corrupted ladder. The magnitude scales with the width differential between adjacent bins and the depth of the walk.

The scope rules explicitly gate on: *"Lens/quoter path: quoted amounts… must match actual pool execution closely enough that integrations are not induced into fund-losing trades."*

### Likelihood Explanation
Pool creation via `MetricOmmPoolFactory.createPool` is **permissionless**. Each bin's `lengthE6` is an independent `uint16` with no uniformity constraint enforced by the factory. Any pool — including all existing production pools — with bins of different widths triggers the bug on every `getLiquidityDepth` call that spans more than one bid bin. This is a standard, expected pool topology (e.g., wider bins near the spread, narrower bins deep in the book).

### Recommendation
Replace the `lenAbove` lookup with the current bin's own `lengthE6`, mirroring the correct pattern in `_fillAsks`:

```solidity
// Before the lowerX64/upperX64 computation, replace:
(,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1));
walkDistE6 -= int256(uint256(lenAbove));

// With:
// (read lengthE6 from binIdx, already fetched below — reorder or hoist the read)
walkDistE6 -= int256(uint256(lengthE6)); // lengthE6 of bin b, not b+1
```

The `lengthE6` of `binIdx` is already fetched on line 500; the fix is to hoist that read above the `walkDistE6` update and use it there instead of the separate `lenAbove` lookup.

### Proof of Concept
Deploy a pool with two bins below `curBinIdx`: bin −1 with `lengthE6 = 5_000` and bin −2 with `lengthE6 = 10_000`. Set `curBinDistFromProvidedPriceE6 = 0`.

**Expected `walkDistE6` values:**
- Bin −1: `0 − 5_000 = −5_000`
- Bin −2: `−5_000 − 10_000 = −15_000`

**Actual `walkDistE6` values (buggy):**
- Bin −1: `lenAbove = lengthE6(curBinIdx=0)`. If bin 0 has `lengthE6 = 20_000`: `walkDistE6 = 0 − 20_000 = −20_000` ✗
- Bin −2: `lenAbove = lengthE6(−1) = 5_000`: `walkDistE6 = −20_000 − 5_000 = −25_000` ✗

Call `getLiquidityDepth(pool, 3)`. Assert `bids[1].binAvgExecPriceX64` diverges from the price computed by `simulateSwapAndRevert` for the same cumulative amount. Then execute a sell sized to consume bin −1 using the ladder's quoted price; observe the actual token1 received is less than the ladder implied. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L440-442)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    ctx.cumDistE6 += int256(uint256(lengthE6));
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L1200-1200)
```text
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPool.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
