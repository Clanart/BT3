### Title
Truncated NFT royalty amount due to division-before-multiplication in `compute_royalty_amount` / `royalty_calculation` - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount()` and the static helper `NFTWallet.royalty_calculation()` compute the royalty owed to an NFT creator by dividing the offered/requested fungible amount by the royalty split count *before* multiplying by the royalty percentage. This ordering causes unnecessary integer truncation and results in the royalty recipient receiving less than the mathematically correct amount whenever more than one royalty-enabled NFT is bundled in a single offer (or more than one fungible-asset splits the royalty).

### Finding Description
`compute_royalty_amount` at [1](#0-0)  computes:

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

This performs `abs(offered_amount) // royalty_split` first (integer division), then multiplies by `percentage`, then divides by `MAX_ROYALTY_BASIS_POINTS` (10000). Because the first division discards a remainder before the multiplication, the final result can be strictly smaller than the mathematically correct `abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS`.

The same pattern is duplicated in the static helper `NFTWallet.royalty_calculation()` used by the wallet RPC endpoint `nft_calculate_royalties`:

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

`compute_royalty_amount` is invoked directly in the offer-construction path `NFTWallet.make_nft1_offer()` whenever multiple royalty-enabled NFTs are offered/requested together (i.e., `royalty_split > 1`), splitting a single fungible amount's royalty across several NFT creators:

```python
for launcher_id, address, percentage in required_royalty_info:
    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
``` [3](#0-2) 

This `royalty_split`/`request_side_royalty_split` value is derived directly from the offer content — the number of royalty-enabled NFTs requested or offered together, as counted in the same function:

```python
offer_side_royalty_split: int = 0
request_side_royalty_split: int = 0
for asset, amount in royalty_nft_asset_dict.items():
    if amount > 0:
        request_side_royalty_split += 1
    elif amount < 0:
        offer_side_royalty_split += 1
``` [4](#0-3) 

Since offers are constructed locally by the wallet creating (or responding to) a trade, any wallet user — the maker or taker of an NFT trade offer bundling more than one royalty-enabled NFT — can trigger this truncation, causing the royalty amount actually paid on-chain to the NFT creator's `royalty_address` to be smaller than the correct proportional share.

### Impact Explanation
The bug causes the royalty-bearing party (the NFT's `royalty_address` recipient, typically the original creator) to receive fewer mojos than they are entitled to under the negotiated `royalty_percentage`, whenever `royalty_split > 1` (i.e., an offer bundles 2 or more royalty NFTs on the same side). This is a concrete fund-loss/underpayment defect in offer settlement royalty accounting, reachable by any wallet participant constructing or accepting an NFT offer — no privileged access or malicious peer required. The lost amount is deterministic given the offer parameters and can be non-trivial for larger `royalty_split` and `offered_amount` values with unfavorable remainders.

### Likelihood Explanation
Likelihood is moderate to high: any user creating an NFT offer that bundles multiple royalty-enabled NFTs on one side of the trade (a normal, documented use case — "complex" multi-NFT offers, as exercised in `test_complex_nft_offer`) will trigger the truncated computation for every trade with such a bundle whenever the division has a non-zero remainder before the multiplication step.

### Recommendation
Reorder the arithmetic so multiplication happens before both divisions, matching the referenced fix pattern (multiply first, divide last), e.g.:

```python
amount = abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS
```

and similarly in `NFTWallet.royalty_calculation()`:

```python
"amount": abs(amount) * percentage // len(royalty_assets_dict) // MAX_ROYALTY_BASIS_POINTS,
```

Care should be taken to verify no overflow occurs given `uint64` bounds before the final divisions (as already partially guarded by `test_large_amount_no_overflow` in the existing test suite [5](#0-4) ).

### Proof of Concept
Using `compute_royalty_amount(offered_amount=-5, royalty_split=2, percentage=9999)`:
- Buggy (current) order: `abs(-5) // 2 = 2`; `2 * 9999 = 19998`; `19998 // 10000 = 1` → royalty paid = 1.
- Correct order: `abs(-5) * 9999 = 49995`; `49995 // 2 = 24997`; `24997 // 10000 = 2` → royalty owed = 2.

The current implementation underpays the royalty recipient by 1 mojo in this case (and similarly in any case where the intermediate remainder from `// royalty_split` combined with the multiplication would have produced a larger floor result), confirming the fund-loss precision defect at [6](#0-5) .

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L848-855)
```python
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
                )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L888-894)
```python
        offer_side_royalty_split: int = 0
        request_side_royalty_split: int = 0
        for asset, amount in royalty_nft_asset_dict.items():  # requested non fungible items
            if amount > 0:
                request_side_royalty_split += 1
            elif amount < 0:
                offer_side_royalty_split += 1
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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L35-39)
```python
def test_large_amount_no_overflow() -> None:
    amount = -(2**63)
    result = compute_royalty_amount(offered_amount=amount, royalty_split=1, percentage=5000)
    assert result == uint64(2**63 // 2)
    assert result < abs(amount)
```
