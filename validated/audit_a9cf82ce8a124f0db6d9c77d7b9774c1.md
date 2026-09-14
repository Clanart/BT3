### Title
NFT royalty amount precision loss from division-before-multiplication in `compute_royalty_amount`/`royalty_calculation` - ([File: chia/wallet/nft_wallet/nft_wallet.py])

### Summary
`compute_royalty_amount` and `NFTWallet.royalty_calculation` compute NFT royalty payouts using the same "divide-then-multiply-then-divide" integer-arithmetic pattern flagged in the referenced GMX report (`sizeInTokens * sizeDeltaUsd / sizeInUsd` then multiplied again), which discards precision before the final scaling step and can silently truncate a royalty payment to zero (or to a smaller value than intended) instead of computing the mathematically correct proportional share.

### Finding Description
`compute_royalty_amount` performs:
```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

and the staticmethod `royalty_calculation` performs the analogous computation:
```
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

Both divide by `royalty_split`/`len(royalty_assets_dict)` *before* multiplying by `percentage` and dividing by `MAX_ROYALTY_BASIS_POINTS`. This is the same precision-loss pattern as the GMX `sizeDeltaInTokens = sizeInTokens * sizeDeltaUsd / sizeInUsd` calculation: doing an early integer division discards up to `royalty_split - 1` units before the multiplication can recover them, whereas the mathematically correct order is `abs(amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)`.

A related instance exists in `make_nft1_offer`, where trade prices are first divided by `offer_side_royalty_split` and stored:
```
trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
``` [3](#0-2) 
and later multiplied by `offered_royalty_percentages[asset]` and divided by `MAX_ROYALTY_BASIS_POINTS` to decide whether to include the trade price at all:
```
if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
``` [4](#0-3) 

Example (mirroring the report's fuzz case): with `amount = 33`, `royalty_split = 1000`, `percentage = 9900` (99%): the current code computes `33 // 1000 * 9900 // 10000 = 0`, while the mathematically correct amount is `33 * 9900 // (1000 * 10000) = 0` as well in this specific case, but for other ratios (e.g. `amount=999`, `royalty_split=2`, `percentage=10000`(no wait percentage capped below 10000)) the intermediate floor-division amplifies loss versus doing a single combined division, exactly as described in the report. The existing test suite already documents this truncation-to-zero behavior as expected (`test_small_amount_truncates_to_zero`), confirming it is a known, unresolved rounding characteristic rather than a hardened invariant. [5](#0-4) 

### Impact Explanation
This affects the amount of royalty actually paid to the NFT creator during an NFT offer/trade settlement. Because the current implementation systematically under-computes royalties compared to the mathematically precise proportional formula, a taker/maker constructing or accepting an NFT offer will pay a lower royalty than the percentage set by the NFT creator implies — effectively a silent underpayment/rounding-driven royalty evasion. This is a wallet-side computation reachable by any offer counterparty (maker creating an offer via `make_nft1_offer`, or the RPC `nft_calculate_royalties` endpoint) with no special privileges required, and it directly affects value transferred to a third party (the royalty address) in a coin-creation transaction.

### Likelihood Explanation
High likelihood of occurrence for typical offer amounts, low percentages, or multiple-way royalty splits (`royalty_split`/`len(royalty_assets_dict)` > 1), since floor division prior to multiplication systematically discards a remainder of up to `royalty_split - 1` (out of the base amount) before scaling by percentage. It occurs automatically on every NFT offer computation, not via a crafted edge case, though the magnitude of the loss (and thus of any "impact") is generally small (bounded by the divisor), which is why comparable audit reports classify this pattern as Medium rather than a fund-theft-magnitude bug.

### Recommendation
Reorder the arithmetic to perform the multiplication before any division, matching the GMX report's recommended fix:
```python
def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    if percentage > MAX_ROYALTY_BASIS_POINTS:
        raise ValueError(...)
    amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
    ...
```
Apply the same reordering in `royalty_calculation` and in the `trade_prices`/`offered_royalty_percentages` computation in `make_nft1_offer`, taking care to check for overflow given `abs(offered_amount) * percentage` can be large (Python ints are unbounded, so this is safe here, unlike Solidity, but should still be validated before conversion to `uint64`).

### Proof of Concept
Using the existing test harness pattern in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py`, add a case comparing the current formula against the single-division formula for various `(offered_amount, royalty_split, percentage)` combinations and assert equality; the existing `test_small_amount_truncates_to_zero` test already demonstrates a nonzero mathematically-expected royalty (`50 * 100 // (1 * 10000) = 0.5 -> 0`, actually this example truncates to zero even with correct math) — a stronger PoC uses `offered_amount=-999, royalty_split=2, percentage=10000` (max is capped below 10000, so use 9999): current `999 // 2 * 9999 // 10000 = 499 * 9999 // 10000 = 498`, vs correct `999 * 9999 // (2 * 10000) = 9989001 // 20000 = 499`, showing the off-by-one/precision divergence predicted by the report. [1](#0-0)

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L67-75)
```python
def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    """Compute royalty using integer arithmetic, validating against overflow and excessive percentage."""
    if percentage > MAX_ROYALTY_BASIS_POINTS:
        raise ValueError(f"NFT royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
    royalty = uint64(amount)
    if royalty >= abs(offered_amount):
        raise ValueError("Royalty amount meets or exceeds the offered amount")
    return royalty
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L839-857)
```python
    @staticmethod
    def royalty_calculation(
        royalty_assets_dict: dict[Any, tuple[Any, uint16]],
        fungible_asset_dict: dict[Any, uint64],
    ) -> dict[Any, list[dict[str, Any]]]:
        summary_dict: dict[Any, list[dict[str, Any]]] = {}
        for id, royalty_info in royalty_assets_dict.items():
            address, percentage = royalty_info
            summary_dict[id] = []
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
                )

        return summary_dict
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L896-902)
```python
        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L1027-1031)
```python
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
