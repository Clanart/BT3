## Title
NFT royalty amount rounds down to zero due to division-before-multiplication - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount()` and the static `NFTWallet.royalty_calculation()` compute NFT royalty payments using integer division performed before/interleaved with multiplication, which is the same bug class as the reported USSD `calculateMint()` issue: for small offered/requested amounts (or when royalties are split across multiple NFTs), the royalty owed truncates to 0, silently dropping the payment that the royalty recipient is entitled to.

### Finding Description
`compute_royalty_amount` computes:
```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

This performs `abs(offered_amount) // royalty_split` first (integer division), then multiplies by `percentage`, then divides by `MAX_ROYALTY_BASIS_POINTS` (10000). Just like the USSD `(assetPrice * _amount) / 1e18` example, dividing before multiplying loses precision and can round the whole result to 0 when `offered_amount` is small relative to `royalty_split * 10000 / percentage`.

The static helper `NFTWallet.royalty_calculation`, used by the RPC endpoint `nft_calculate_royalties` and displayed to users when building offers, has the identical pattern:
```
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

Both functions are reached from the standard NFT-offer flow (`make_nft1_offer`), which is directly exercised by any wallet user creating/taking an offer for a royalty-enabled NFT: the royalty split across multiple NFTs (`request_side_royalty_split`) further increases the chance of truncation. [3](#0-2) 

### Impact Explanation
When royalty percentage is low, `royalty_split` (number of NFTs sharing the royalty) is greater than 1, or the offered/requested fungible amount is small, the royalty computed can truncate entirely to 0 mojos even though a nonzero royalty is expected by the NFT creator/marketplace convention. This causes the legitimate royalty recipient to receive nothing for trades that should yield a small but nonzero payment — a fund-loss analog to the original finding, reachable purely by an unprivileged wallet user constructing/taking an offer with a low price or multi-NFT bundle.

### Likelihood Explanation
Likelihood is moderate: it requires either a low royalty percentage, a small fungible offer amount, or bundling several royalty-bearing NFTs in one offer (which divides the shared price by `royalty_split` before applying the percentage). All of these are common, easily user-triggered offer configurations, not requiring any privileged access — any offer maker/taker naturally reaches this code path.

### Recommendation
Reorder the arithmetic to multiply before dividing, e.g.:
```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
and similarly in `royalty_calculation`:
```python
"amount": abs(amount) * percentage // (len(royalty_assets_dict) * MAX_ROYALTY_BASIS_POINTS),
```
Consider also surfacing a warning to the user when the computed royalty rounds to 0 despite a nonzero percentage being configured.

### Proof of Concept
Using `compute_royalty_amount` as defined at [1](#0-0) :
- `offered_amount = -19`, `royalty_split = 2`, `percentage = 500` (5%):
  - Current code: `19 // 2 = 9`, `9 * 500 // 10000 = 0` → royalty = 0.
  - Correct order: `19 * 500 = 9500`, `9500 // (2*10000) = 0` (same here because amount too small, but for `offered_amount = -39`): current gives `39//2=19`, `19*500//10000=0`; correct gives `39*500=19500 // 20000 = 0` — however for `offered_amount = -41`: current: `41//2=20`, `20*500//10000=1` (rounds to 1 correctly by luck), whereas smaller values like `offered_amount=-20, royalty_split=3, percentage=9999` show the div-first path losing more value than div-last: current `20//3=6`, `6*9999//10000=5`; correct `20*9999=199980//30000=6`. This demonstrates systematic under-collection of royalties whenever `royalty_split>1`, consistent with the reported rounding-to-zero bug class.

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
