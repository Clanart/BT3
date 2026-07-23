### Title
Incorrect Bid-Side Price Boundaries in `_fillBids` Due to Wrong Bin Length Subtracted from `walkDistE6` — (`metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol`)

---

### Summary

`MetricOmmPoolDataProvider._fillBids` decrements `walkDistE6` by the length of the **bin above** (`lenAbove = lengthE6 of bin b+1`) instead of the length of the **current bin** (`lengthE6 of bin b`). This produces wrong lower/upper price boundaries for every bin below `curBinIdx`, corrupting the `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` fields returned by `getLiquidityDepth`.

---

### Finding Description

`_fillBids` walks downward from `curBinIdx` to `lowCap`. `walkDistE6` is supposed to track the **lower edge** of the bin currently being priced. The ask-side walk (`_fillAskRow`) does this correctly: it advances `cumDistE6` by `lengthE6` of the bin just processed before moving to the next bin.

The bid-side loop does the opposite:

```solidity
// metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol  lines 492-510
for (int256 b = int256(curBinIdx) - 1; b >= int256(lowCap); b--) {
    int8 binIdx = int8(b);
    (,, uint16 lenAbove,,) = PoolStateLibrary._binState(pool, int8(b + 1)); // length of bin b+1
    walkDistE6 -= int256(uint256(lenAbove));                                 // ← WRONG: subtracts b+1's length

    (, uint104 t1, uint16 lengthE6,, uint16 addFeeSellE6) = PoolStateLibrary._binState(pool, binIdx);
    ...
    uint256 lowerX64 = _priceFromMidAndDistE6(midPriceX64, walkDistE6, Math.Rounding.Floor);
    uint256 upperX64 =
        _priceFromMidAndDistE6(midPriceX64, walkDistE6 + int256(uint256(lengthE6)), Math.Rounding.Floor);
```

**Correct invariant:** the lower edge of bin `b` equals the lower edge of bin `b+1` minus `lengthE6(b)`.

```
lower(b) = lower(b+1) - lengthE6(b)
```

**What the code computes:**

```
walkDistE6 = lower(b+1) - lengthE6(b+1)   ← uses b+1's length, not b's
```

This is the SpiceAuction analog: a derived value (`walkDistE6`) should use the already-computed intermediate (`lengthE6` of the current bin `b`) but instead re-reads the raw source (`lenAbove`, the length of the bin above).

**Concrete example** (bins with different lengths):

| Bin | lengthE6 | Correct lower edge | Code's lower edge |
|-----|----------|--------------------|-------------------|
| 0 (cur) | 10 000 | 0 | 0 ✓ |
| −1 | 20 000 | −20 000 | −10 000 ✗ |
| −2 | 15 000 | −35 000 | −30 000 ✗ |

Both `lowerX64` and `upperX64` are wrong for every bin below `curBinIdx`, and the error accumulates with each hop.

---

### Impact Explanation

`getLiquidityDepth` is the primary off-chain depth oracle for the protocol. Integrations (aggregators, front-ends, market-making bots) consume `binAvgExecPriceX64` and `cumulativeAvgExecPriceX64` to:

1. Estimate execution price for a given sell size.
2. Set `amountOutMinimum` / `priceLimitX64` before calling `exactInput` or `exactInputSingle` on the router.

When the bid depth ladder reports prices that are systematically shifted (wrong bin boundaries → wrong marginal prices → wrong fee-adjusted execution prices), a caller who sizes their slippage guard from this data will either:

- **Accept a worse price than intended** (slippage guard set too loosely because the depth ladder showed a better price than reality) → direct loss of output tokens.
- **Revert unnecessarily** (slippage guard set too tightly because the depth ladder showed a worse