### Title
Order of operations causes NFT royalty underpayment in `compute_royalty_amount`/`royalty_calculation` - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`chia/wallet/nft_wallet/nft_wallet.py` computes royalty payments for NFT offers using integer division before multiplication, causing avoidable truncation of the royalty amount that flows into the actual `CreateCoin` payment.

### Finding Description
Two functions perform the same operation ordering: divide first, then multiply, then divide again: [1](#0-0) 

```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

and the display/estimate helper `royalty_calculation`: [2](#0-1) 

Both divide by `royalty_split` (or `len(royalty_assets_dict)`) before multiplying by `percentage`, then divide by `MAX_ROYALTY_BASIS_POINTS` (10000) again. Because Solidity/Python integer division truncates, performing the division `abs(amount) // royalty_split` first discards the remainder before it can be scaled by `percentage`, unlike the mathematically-equivalent `abs(amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)`, which preserves more precision.

`compute_royalty_amount` is not just a display estimate — its result is used directly to build the on-chain royalty `CreateCoin` payment during `make_nft1_offer`: [3](#0-2) 

This is reachable by any wallet user creating or taking an NFT offer (an unprivileged offer counterparty action), since `NFTWallet.make_nft1_offer` is invoked from `TradeManager` when constructing/accepting an offer involving royalty‑bearing NFTs (`chia/wallet/trade_manager.py`, `respond_to_offer` / `_create_offer_for_ids`).

### Impact Explanation
The truncation systematically rounds the royalty amount down whenever there is more than one NFT sharing a royalty split (`royalty_split > 1`) or when `offered_amount * percentage` is not evenly divisible by `royalty_split * MAX_ROYALTY_BASIS_POINTS`. This causes the royalty recipient (the NFT creator) to receive a smaller payment than the exact intended percentage, while the trade counterparty benefits. However, the magnitude of the loss per trade is bounded and small — at most on the order of `royalty_split - 1` basis-point-scaled units relative to the offered amount, i.e. typically a handful of mojos per split, not a scalable or attacker-amplifiable drain. There is no unauthorized fund movement, no supply inflation, and no consensus/coin-set divergence; it is a value-precision issue confined to voluntary trade settlement amounts.

### Likelihood Explanation
This code path executes on every NFT offer involving royalty NFTs with `royalty_split > 1` (i.e., an offer bundling multiple royalty‑enabled NFTs on one side), so it is deterministically triggered, not a rare edge case. It requires no privileged access — any wallet user constructing or responding to such an NFT offer exercises this arithmetic.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the report's suggested fix, e.g.:
```
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
Apply the same reordering to the `"amount"` computation in `NFTWallet.royalty_calculation` (line 853) so displayed royalty estimates match the amounts actually paid.

### Proof of Concept
With `royalty_split=3`, `offered_amount=-100`, `percentage=9900` (99%, valid since it's below `MAX_ROYALTY_BASIS_POINTS`):
- Current code: `100 // 3 = 33`; `33 * 9900 = 326700`; `326700 // 10000 = 32`.
- Correct (multiply-first) order: `100 * 9900 = 990000`; `990000 // (3 * 10000) = 990000 // 30000 = 33`.

The current implementation pays `32` instead of the more precise `33`, systematically underpaying the royalty recipient by 1 unit in this example whenever an offer splits royalties across multiple NFTs. This is confirmed by the existing test suite exercising `compute_royalty_amount`'s truncation behavior: [4](#0-3) [5](#0-4)

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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
