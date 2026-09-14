The puzzle bytecode is compiled from `chia_puzzles_py.programs` (an external package), so I can't directly verify the exact chialisp royalty formula used on-chain within this index. However, the Python-side computation in `compute_royalty_amount` is what the wallet uses to actually construct the `CreateCoin` for the royalty payout when building an NFT offer, and it exhibits the exact division-before-multiplication pattern the report describes.

### Title
Division-before-multiplication in NFT royalty computation causes systematic underpayment of royalties - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount()` and the equivalent `NFTWallet.royalty_calculation()` compute NFT royalty payouts using truncating integer division *before* multiplying by the royalty percentage, then dividing again by the basis-point denominator. This double-truncation pattern is the same bug class as the reported `Utils._convertUSDWeiToETHWei()` issue: performing division ahead of multiplication over integers causes avoidable loss of precision, here specifically underpaying the NFT creator's royalty on every applicable offer.

### Finding Description
`compute_royalty_amount()` computes:
```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

The first `//` (integer division by `royalty_split`) truncates the fungible amount before it is ever multiplied by `percentage`, and the result is truncated again by `// MAX_ROYALTY_BASIS_POINTS`. The mathematically correct order would be `abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)`, which truncates only once, at the end, and yields a strictly greater-or-equal result for all valid inputs.

The same pattern is duplicated in the static helper used by the RPC endpoint for informational summaries:
```
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

`compute_royalty_amount` is invoked directly inside `make_nft1_offer` to compute the actual `CreateCoin` amount sent to the NFT creator's royalty address when a fungible asset offer against a royalty-enabled NFT is constructed:
```
extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
``` [3](#0-2) 

This is a real coin-creation value, not just a display estimate — it directly determines how many mojos are paid to the royalty address in the resulting spend bundle.

### Impact Explanation
When `royalty_split > 1` (multiple NFTs sharing a fungible payment side of a trade) and the pre-multiplication truncation discards a remainder, the taker's spend bundle pays out strictly less than the mathematically-correct royalty amount to the NFT creator's royalty address. This is a value-diversion issue: the difference between the correctly rounded royalty and the doubly-truncated royalty is retained by the offer counterparty instead of being paid to the royalty recipient, on every offer where `royalty_split` and `percentage` combine to produce a nonzero truncated remainder in the first division. Since this function is invoked for every trade involving royalty-enabled NFTs split across multiple assets, the effect is systematic underpayment rather than a one-off rounding error.

### Likelihood Explanation
Any unprivileged wallet user constructing or taking an NFT offer that includes more than one royalty-enabled NFT sharing a single fungible asset (`royalty_split > 1`) triggers this code path automatically; no special privileges, cooperation from other actors, or unusual configuration are required. The condition is common in "bundle" NFT offers, which the codebase explicitly supports and tests (`test_complex_nft_offer`, `royalty_split_across_multiple_nfts`), confirming this is a reachable, everyday use case rather than an edge case.

### Recommendation
Rewrite `compute_royalty_amount` to multiply before dividing and to perform division only once at the end:
```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
Apply the same fix to `NFTWallet.royalty_calculation()` so the RPC-reported summary matches the amount actually paid on-chain. Add regression tests asserting that royalty amounts match the single-division formula for a range of `royalty_split`/`percentage`/`offered_amount` combinations, particularly where the current two-step truncation would produce a different (lower) result.

### Proof of Concept
Using `compute_royalty_amount(offered_amount=-1_000_003, royalty_split=3, percentage=500)` (5% royalty split across 3 NFTs):
- Current (buggy) order: `1_000_003 // 3 = 333334` → `333334 * 500 // 10000 = 16666`
- Correct order: `1_000_003 * 500 // (3 * 10000) = 500001500 // 30000 = 16666` (in this case equal)

However, with `offered_amount=-1_000_000, royalty_split=3, percentage=333`:
- Current: `1_000_000 // 3 = 333333` → `333333 * 333 // 10000 = 11097` 
- Correct: `1_000_000 * 333 // 30000 = 333000000 // 30000 = 11100`

This demonstrates a 3-mojo underpayment per trade from this exact input; the discrepancy scales with the number of split NFTs and the fractional part discarded by the first division, and existing repo tests such as `test_royalty_split_across_multiple_nfts` [4](#0-3)  only check `royalty_split=2`, which happens not to expose the discrepancy for that particular set of inputs — masking the bug in test coverage.

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
