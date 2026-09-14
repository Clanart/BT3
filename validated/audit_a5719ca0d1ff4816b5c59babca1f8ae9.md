## Analog Found: Divide-before-multiply precision loss in NFT royalty computation

### Title
Divide-before-multiply precision loss in `compute_royalty_amount()` / `NFTWallet.royalty_calculation()` underpays NFT royalty holders in offers - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`chia/wallet/nft_wallet/nft_wallet.py` computes NFT trade royalties by dividing the offered/requested amount by the royalty split **before** multiplying by the royalty percentage and dividing by the basis-point denominator, causing avoidable integer-truncation precision loss, exactly analogous to the reported GMX `getFundingFeeAmount()` divide-before-multiply bug.

### Finding Description
`compute_royalty_amount()` computes:
```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

The same pattern is used in `NFTWallet.royalty_calculation()`:
```
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

Both perform integer division (`// royalty_split` or `// len(royalty_assets_dict)`) **before** the multiplication by `percentage`, then divide again by `MAX_ROYALTY_BASIS_POINTS` (10000). When `royalty_split > 1` (i.e., an offer bundles multiple royalty-enabled NFTs on one side, per `offer_side_royalty_split`/`request_side_royalty_split` counters built in `make_nft1_offer()`) [3](#0-2) , the early truncation from `abs(amount) // royalty_split` discards remainder bits that the later multiplication by `percentage` could have recovered had the operations been ordered as `abs(amount) * percentage // MAX_ROYALTY_BASIS_POINTS // royalty_split` or similar mul-before-div ordering.

Concretely, for `offered_amount=-100`, `royalty_split=3`, `percentage=9999` basis points:
- Actual (divide-first) result: `100 // 3 = 33`; `33 * 9999 = 329967`; `// 10000 = 32`.
- Mathematically correct floor: `100 * 9999 // 3 // 10000 = 999900 // 30000 = 33`.

This is a 1-unit (and in general, up to `royalty_split - 1` unit-scale) underpayment to the royalty recipient every time this code path executes with a non-exact-dividing split, systematically biased in one direction (favoring the offer maker/taker paying less royalty) rather than rounding neutrally.

This code is directly reachable by any wallet user creating or estimating an NFT offer via `NFTWallet.make_nft1_offer()` [4](#0-3)  or via the public RPC `nft_calculate_royalties` [5](#0-4) , both of which are exposed to any wallet-owning caller constructing/accepting offers, with no privileged access required.

### Impact Explanation
Royalty-enabled NFT creators/holders (offer counterparties who are NOT the submitter) systematically receive slightly less than their contractually configured royalty percentage whenever an offer bundles more than one royalty-bearing NFT on a side (or fungible assets requested against multiple NFTs). This is a concrete, deterministic underpayment of value owed to a third party (the royalty address) baked into consensus-visible coin creation amounts in the settlement spend bundle — i.e., unauthorized reduction of committed royalty payouts, reachable purely by a spend-bundle submitter's own offer construction. However, the magnitude is bounded (loses only fractional basis-point remainder, at most `royalty_split - 1` scaled units) and requires the offer maker to intentionally or incidentally combine multiple royalty NFTs to manifest; it does not enable outright fund theft or double-spend, only systematic underpayment of a specific side.

### Likelihood Explanation
Any user creating a multi-NFT offer (`royalty_split > 1`) that doesn't evenly divide against the percentage will trigger this on every affected offer; it is not a rare edge case, but the per-event value lost is very small (fractional basis-point rounding).

### Recommendation
Reorder operations to multiply before dividing, e.g.:
```python
amount = abs(offered_amount) * percentage // MAX_ROYALTY_BASIS_POINTS // royalty_split
```
or better, keep numerator combined until final division:
```python
amount = (abs(offered_amount) * percentage) // (MAX_ROYALTY_BASIS_POINTS * royalty_split)
```
Apply the same fix to `NFTWallet.royalty_calculation()`'s per-asset amount calculation at [6](#0-5) .

### Proof of Concept
Using the existing test harness `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` [7](#0-6) , add:
```python
def test_precision_loss_divide_before_multiply() -> None:
    # correct floor: 100*9999//3//10000 == 33
    # actual (divide-before-multiply) result:
    result = compute_royalty_amount(offered_amount=-100, royalty_split=3, percentage=9999)
    assert result == uint64(32)  # underpaid by 1 unit vs mathematically correct 33
```
This demonstrates the royalty recipient is shorted relative to the mathematically correct proportional share.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L888-902)
```python
        offer_side_royalty_split: int = 0
        request_side_royalty_split: int = 0
        for asset, amount in royalty_nft_asset_dict.items():  # requested non fungible items
            if amount > 0:
                request_side_royalty_split += 1
            elif amount < 0:
                offer_side_royalty_split += 1

        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
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

**File:** chia/wallet/wallet_rpc_api.py (L2748-2757)
```python
    async def nft_calculate_royalties(self, request: NFTCalculateRoyalties) -> NFTCalculateRoyaltiesResponse:
        return NFTCalculateRoyaltiesResponse.from_json_dict(
            NFTWallet.royalty_calculation(
                {
                    asset.asset: (asset.royalty_address, uint16(asset.royalty_percentage))
                    for asset in request.royalty_assets
                },
                {asset.asset: asset.amount for asset in request.fungible_assets},
            )
        )
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L24-26)
```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)
```
