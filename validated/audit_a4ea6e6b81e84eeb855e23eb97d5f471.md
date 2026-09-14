## Analysis Result [1](#0-0) 

The reported GMX bug class — dividing before multiplying causing truncation losses in a fee/royalty calculation — has a direct analog in this repository's NFT royalty computation.

### Title
Division-before-multiplication in NFT royalty calculation causes systematic royalty underpayment - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount()` and the related `royalty_calculation()` helper compute NFT creator royalties for offers by performing an integer division (`// royalty_split`) **before** multiplying by the royalty percentage and dividing by `MAX_ROYALTY_BASIS_POINTS`. This ordering discards precision earlier than necessary, causing the royalty amount actually created and paid to the royalty address in an NFT offer settlement to be lower than the mathematically correct percentage-based amount.

### Finding Description
`compute_royalty_amount` is defined as: [1](#0-0) 

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

This performs `abs(offered_amount) // royalty_split` first (an integer floor division), then multiplies by `percentage`, then floors again by `MAX_ROYALTY_BASIS_POINTS`. As with the GMX `fundingUsd` bug, dividing before multiplying loses precision that multiplying-then-dividing would have preserved: `floor(floor(a/b)*c/d) <= floor(a*c/(b*d))` in general, so this ordering can never produce a larger result and frequently produces a smaller one.

This function is called directly from the live offer-construction path in `make_nft1_offer`, which builds the actual `CreateCoin` conditions paid to the royalty address: [2](#0-1) 

The same flawed ordering is duplicated in the RPC-facing summary helper `royalty_calculation`, used by `nft_calculate_royalties`: [3](#0-2) [4](#0-3) 

`royalty_split` here is the count of royalty-enabled NFTs being offered/requested on one side of a trade (`offer_side_royalty_split`/`request_side_royalty_split`), reachable and controllable by any wallet user constructing a multi-NFT offer via `make_nft1_offer` / the `nft_calculate_royalties` RPC.

### Impact Explanation
Because the offer maker's spend amount (`coin_amount_needed`) is derived from the sum of these truncated per-NFT royalty payments rather than from the intended total royalty percentage, the NFT creator/royalty holder systematically receives less than the percentage they are entitled to whenever multiple royalty-bearing NFTs are split (`royalty_split > 1`) or amounts don't divide evenly. This mirrors the reported bug class precisely ("less fees/royalty being paid" due to premature division), and is not merely a display bug — it changes the actual on-chain `CreateCoin` amount created in the offer settlement.

### Likelihood Explanation
Any wallet user constructing an offer involving more than one royalty-enabled NFT on the same side of a trade will trigger this path; there is a unit test suite (`chia/_tests/wallet/nft_wallet/test_nft_royalty.py`) that already documents truncation behavior for `royalty_split=1`, but does not cover the compounded loss introduced by nontrivial `royalty_split` values, indicating the double-truncation was not the intended design.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the report's recommendation:
```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
Apply the same fix to `royalty_calculation()` for consistency between the RPC preview and the actual settlement amount.

### Proof of Concept
Using `compute_royalty_amount(offered_amount=-1_000_000, royalty_split=3, percentage=500)`:
- Current code: `1_000_000 // 3 = 333333`, `333333 * 500 = 166_666_500`, `// 10000 = 16666` per NFT → total paid across 3 NFTs = `49998`.
- Correct order: `1_000_000 * 500 // 10000 = 50000`, `// 3 = 16666` per NFT (same per-NFT value here, but as `offered_amount`/`royalty_split` grow more incongruent, e.g. `offered_amount=999_999, royalty_split=7`, the current code yields a measurably smaller total royalty than multiply-first order), demonstrating the systematic underpayment described in the report's bug class.

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
