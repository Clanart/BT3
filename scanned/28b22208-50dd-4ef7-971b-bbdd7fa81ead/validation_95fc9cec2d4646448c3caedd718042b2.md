### Title
Synthetic Ratio Precision Loss Truncates Mid to Zero, Permanently Halting Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider._getBidAndAskPrice()` computes a synthetic mid price as `Math.mulDiv(mid1, ORACLE_DECIMALS, mid2)` where `ORACLE_DECIMALS = 1e8`. When the base token is sufficiently cheap relative to the quote token, integer division truncates the result to `0`. The zero mid propagates into `_computeBidAsk`, which returns the `(0, type(uint128).max)` failure sentinel, causing `getBidAndAskPrice()` to revert with `FeedStalled()` on every swap. The pool is permanently bricked for trading while oracle data is entirely valid.

---

### Finding Description

In `AnchoredPriceProvider._getBidAndAskPrice()`, when `quoteFeedId` is set (synthetic two-feed mode), the ratio mid price is computed as:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
``` [1](#0-0) 

`ORACLE_DECIMALS` is `1e8`, so the formula is effectively:

```
syntheticMid = basePriceIn8Dec * 1e8 / quotePriceIn8Dec
```

For this to be non-zero, the ratio `basePrice / quotePrice` must be ≥ `1e-8`. If the base token is cheap (e.g., a meme token at `$0.000001`) and the quote token is expensive (e.g., BTC at `$60,000`), the ratio is `~1.67e-11 < 1e-8`, so `syntheticMid` truncates to `0`. [2](#0-1) 

There is **no zero-check on `mid` after the ratio computation**. The code immediately calls `_computeBidAsk(0, spreadBps)`: [3](#0-2) 

Inside `_computeBidAsk`, `_bandEdge(0, ...)` returns `0` for `refBid`, which triggers the guard:

```solidity
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
``` [4](#0-3) 

The `(0, type(uint128).max)` sentinel propagates back to `getBidAndAskPrice()`:

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [5](#0-4) 

Every swap on the pool reverts. The oracle data is valid; only the precision of the ratio representation is insufficient.

---

### Impact Explanation

Any pool whose `AnchoredPriceProvider` is deployed in synthetic mode (`quoteFeedId != bytes32(0)`) with a base/quote price ratio below `1e-8` will have all swap execution permanently blocked. LPs cannot earn fees; traders cannot execute. This satisfies the "broken core pool functionality causing unusable swap flows" impact gate.

---

### Likelihood Explanation

The `quoteFeedId` parameter is an explicit, documented constructor input for synthetic ratio quoting (e.g., BTC/ETH = BTC/USD ÷ ETH/USD). Any pool pairing a low-value token against a high-value quote token (ratio < 1e-8) hits this condition purely from market prices — no privileged action or malicious setup is required. The condition can also be reached transiently if the base token price crashes, permanently bricking the pool at that point.

---

### Recommendation

After computing the synthetic ratio, add an explicit zero-check:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
if (mid == 0) return (0, type(uint128).max);
```

For a more robust fix, increase the internal precision of the ratio by scaling the numerator before dividing (e.g., multiply by `1e18` instead of `1e8`), then adjust `_bandEdge` / `STEP_DENOM` accordingly so downstream Q64 conversion remains correct. This mirrors the WooFi recommendation of moving from 8-decimal to 18-decimal price representation.

---

### Proof of Concept

**Setup:** Deploy `AnchoredPriceProvider` with:
- `baseFeedId` → feed returning `mid = 100` (i.e., `$0.000001` at 8 decimals)
- `quoteFeedId` → feed returning `mid = 6_000_000_000_000` (i.e., `$60,000` at 8 decimals)

**Execution:**

```
syntheticMid = Math.mulDiv(100, 1e8, 6_000_000_000_000)
             = 10_000_000_000 / 6_000_000_000_000
             = 0  (integer truncation)
```

`_computeBidAsk(0, spreadBps)` → `refBid = 0` → returns `(0, type(uint128).max)`.

`getBidAndAskPrice()` reverts with `FeedStalled()`.

Every subsequent call to the pool's `swap()` reverts. The pool is permanently unusable for trading despite both oracle feeds returning fresh, valid data.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L43-46)
```text
    uint256 internal constant Q64 = 1 << 64;

    uint256 internal constant ORACLE_DECIMALS = 1e8;
    uint256 public  constant BPS_BASE_U = 1e18;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L263-271)
```text
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L311-313)
```text
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```
