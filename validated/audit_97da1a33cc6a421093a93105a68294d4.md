No vulnerability found for this question.

The reported bug is specific to the Sway/Fuel `IFP128` fixed-point library's `non_negative` boolean-flag sign logic, where `(self.non_negative && !self.non_negative) || (!self.non_negative && self.non_negative)` is always `false`. Chia's Python/CLVM codebase has no equivalent custom signed-number type with a separate boolean sign flag — CLVM integers are native arbitrary-precision signed integers, and the analogous royalty/arithmetic code in `chia/wallet/nft_wallet/nft_wallet.py`'s `compute_royalty_amount` uses Python's built-in `abs()` on plain integers rather than a hand-rolled sign-flag multiply/divide implementation, so the specific incorrect-boolean-condition sign bug does not translate to a reachable Chia code path. [1](#0-0) [2](#0-1)

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
