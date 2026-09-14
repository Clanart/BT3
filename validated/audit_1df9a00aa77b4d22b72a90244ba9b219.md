This chia-blockchain codebase has a close analog to the Derby "multiplication before division" bug in `compute_royalty_amount`, which computes NFT royalty payment amounts for offers.

### Title
Premature division in `compute_royalty_amount` truncates NFT royalty payouts when royalty is split across multiple NFTs - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount()` divides the offered amount by `royalty_split` *before* multiplying by `percentage` and dividing by `MAX_ROYALTY_BASIS_POINTS`, causing integer-truncation precision loss identical in structure to the reported Derby `Game.sol` bug (division before multiplication). This function is used by `NFTWallet.make_nft1_offer()` to compute the actual mojo amount paid to royalty-enabled NFT holders in `CreateCoin` outputs when an offer is created or taken.

### Finding Description
`compute_royalty_amount` is defined as: [1](#0-0) 

The order of operations is `abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS`. The `// royalty_split` division happens first and can truncate before the multiplication by `percentage` is applied, discarding fractional value that would otherwise have contributed to the final royalty amount. This is exactly the "multiplication before division" precision class described in the report: dividing first loses precision that multiplying first (and dividing only at the very end) would have preserved.

This function is invoked in the real coin-creation path (not just a preview/summary path) inside `make_nft1_offer`, once per royalty-enabled NFT holder, per fungible asset leg of the trade: [2](#0-1) 

The resulting `extra_royalty_amount` is embedded directly into a `CreateCoin` paid to the royalty address as part of the settlement coin spends that both the maker and taker sign into the offer/spend bundle.

A parallel, similarly-shaped truncation exists in the summary-only `royalty_calculation` static method used by the `nft_calculate_royalties` RPC (`abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS`): [3](#0-2) 

### Impact Explanation
Whenever `royalty_split > 1` — i.e., an offer/trade includes more than one royalty-enabled NFT sharing the same fungible payment leg — and `abs(offered_amount)` is not evenly divisible by `royalty_split`, the early division truncates value before the percentage multiplication is applied. This systematically underpays the royalty-address recipient relative to the mathematically precise (multiply-first) calculation. Any wallet user constructing or taking an NFT offer with multiple royalty-bearing NFTs on one side reaches this code path, and the loss is baked into signed, on-chain settlement coin amounts once the offer is accepted — i.e., real value is misallocated in coin creation during offer settlement, matching the "offer settlement value miscalculation" class called out in the validation rules.

### Likelihood Explanation
This triggers deterministically (100% of the time division is inexact) whenever an offer bundles 2+ royalty NFTs on one side against a shared fungible asset — a normal, supported flow (`make_nft1_offer`/`test_complex_nft_offer` explicitly exercise multi-NFT royalty splits). No malicious peer or privileged access is required; any regular offer-maker/taker using this common feature is affected.

### Recommendation
Reorder the arithmetic to multiply before dividing, deferring all division to the end, e.g.:
```python
amount = abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS
```
Apply the same fix to `NFTWallet.royalty_calculation`'s per-asset amount computation so the RPC-reported royalty preview matches the amount actually paid.

### Proof of Concept
Using `compute_royalty_amount(offered_amount=-99, royalty_split=4, percentage=9999)`:
- Current code: `abs(99) // 4 = 24`; `24 * 9999 = 239976`; `239976 // 10000 = 23`.
- Multiply-first (correct) order: `99 * 9999 = 989901`; `989901 // 4 = 247475`; `247475 // 10000 = 24`.

The current implementation returns `23` mojos instead of the precise `24` mojos — a 1-mojo underpayment per calculation that scales with the number of royalty-holders and fungible-asset legs in a trade, and is repeated identically for every offer that splits royalties across multiple NFTs. [4](#0-3)

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
