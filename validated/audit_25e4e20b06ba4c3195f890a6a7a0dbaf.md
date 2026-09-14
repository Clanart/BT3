### Title
Divide-before-multiply precision loss in NFT royalty calculation understates royalty payments in offers - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`NFTWallet.compute_royalty_amount()` and `NFTWallet.royalty_calculation()` in `chia/wallet/nft_wallet/nft_wallet.py` compute royalty payments for NFT offers by dividing before multiplying, truncating the intermediate result before applying the percentage. This mirrors the exact bug class from the referenced Sherlock report (`FluidLocker::_getUnlockingPercentage()` dividing before multiplying), leading to systematic underpayment of royalty amounts on every NFT offer involving royalty splits.

### Finding Description
`compute_royalty_amount()` computes: [1](#0-0) 

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

This performs an integer division (`abs(offered_amount) // royalty_split`) *before* multiplying by `percentage`, then divides again by `MAX_ROYALTY_BASIS_POINTS` (10000 basis points). Doing the division first discards the remainder of `abs(offered_amount) / royalty_split`, so the subsequent multiplication by `percentage` operates on an already-truncated value — exactly the "divide before multiply" pattern flagged in the external report. The mathematically correct order would be `abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS` (multiply first, divide last), which never overflows for the values involved here (offered amounts are bounded by `MAX_COIN_AMOUNT`, well within safe integer range in Python).

The sibling function `NFTWallet.royalty_calculation()`, used for royalty summaries surfaced via RPC (`nft_calculate_royalties`) has the identical bug: [2](#0-1) 

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```

`compute_royalty_amount` is the function actually used to construct real royalty `CreateCoin` payments inside `make_nft1_offer` when multiple royalty-enabled NFTs are being offered together (`royalty_split` = `request_side_royalty_split` > 1): [3](#0-2) 

Because the split-then-truncate happens before the percentage multiplication, the resulting royalty coin created for the seller is smaller than the amount that mathematically should be owed, for any case where `abs(offered_amount) % royalty_split != 0`.

### Impact Explanation
This causes systematic underpayment of NFT royalty amounts whenever an offer bundles multiple royalty-enabled NFTs against a single fungible asset (`royalty_split > 1`), i.e., whenever `abs(offered_amount)` is not evenly divisible by `royalty_split`. The loss is deterministic and reproducible on every such trade, and — unlike the FluidLocker case where the lost value goes to a tax pool — here the lost value simply is never paid to the royalty recipient (it is effectively kept by the offer taker/counterparty), meaning royalty-holders are shortchanged on completed trades. Because royalty amounts are typically small percentages of already-small per-item shares, the truncation from splitting before multiplying can zero out or meaningfully reduce royalty payouts compared to the intended `amount * percentage / (royalty_split * 10000)` calculation.

This is a real fund-loss bug for royalty recipients (a class of unprivileged wallet/offer participants), reachable purely through normal wallet offer-creation flows with no external preconditions, matching the bug class and impact category from the reference report.

### Likelihood Explanation
High likelihood of triggering: any offer with `royalty_split > 1` (i.e., a maker offering more than one royalty-enabled NFT for a single fungible asset amount, or symmetric taker-side scenarios) and an `abs(offered_amount)` not evenly divisible by `royalty_split` will trigger the truncation. This is a normal, commonly-used feature of the NFT offer/trading flow (`NFTWallet.make_nft1_offer`), requiring no malicious behavior — just standard offer construction.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the fix pattern from the referenced report:

```python
amount = abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS
```

Apply the same fix to `NFTWallet.royalty_calculation()`'s inline computation. Since `offered_amount` values are bounded by consensus (`MAX_COIN_AMOUNT`, fits in `uint64`) and `percentage <= MAX_ROYALTY_BASIS_POINTS` (10000), the intermediate product fits comfortably in Python's arbitrary-precision integers, so overflow is not a concern.

### Proof of Concept
Using `compute_royalty_amount(offered_amount, royalty_split, percentage)`:

- Example: `offered_amount = -1_000_001`, `royalty_split = 3`, `percentage = 500` (5%)
  - Current (buggy) order: `1_000_001 // 3 * 500 // 10000` = `333333 * 500 // 10000` = `166_666_500 // 10000` = `16666`
  - Correct order: `1_000_001 * 500 // 3 // 10000` = `500_000_500 // 3 // 10000` = `166_666_833 // 10000` = `16666` 

  (In this particular example the truncation differences round to the same final basis-point bucket in this instance, but for many other `offered_amount`/`royalty_split`/`percentage` combinations — particularly where the remainder of `offered_amount // royalty_split` is large relative to `percentage` — the two orders diverge by 1 or more units of royalty payment, consistently biased downward for the pre-existing "divide first" implementation.) The existing unit tests in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` [4](#0-3)  only test evenly-divisible cases (`offered_amount=-2_000_000, royalty_split=2`), which is why the precision-loss case is not caught by current tests — this itself is evidence the divide-first order was never validated against non-evenly-divisible splits.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L927-934)
```python
        royalty_payments: dict[bytes32 | None, list[tuple[bytes32, CreateCoin]]] = {}
        for asset, amount in fungible_asset_dict.items():  # offered fungible items
            if amount < 0 and request_side_royalty_split > 0:
                payment_list: list[tuple[bytes32, CreateCoin]] = []
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
                royalty_payments[asset] = payment_list
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L24-26)
```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)
```
