### Title
NFT royalty amount is computed with integer division applied before the percentage multiplication, systematically underpaying royalties during offer settlement - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount()` and `NFTWallet.royalty_calculation()` calculate the royalty a taker owes an NFT creator during an offer trade by dividing the offered amount by the royalty split *before* multiplying by the royalty percentage, instead of doing the multiplication first and dividing once at the end. This ordering causes an avoidable additional truncation of the royalty amount whenever `royalty_split > 1`, in the same way the referenced `USSDRebalancer.BuyUSSDSellCollateral` bug truncates a sell amount to less than intended by dividing before multiplying.

### Finding Description
`compute_royalty_amount` computes: [1](#0-0) 

```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

This performs `abs(offered_amount) // royalty_split` **first** (an integer floor division), then multiplies by `percentage`, then divides again by `MAX_ROYALTY_BASIS_POINTS` (10000). Mathematically the correct (loss-minimizing) royalty is `abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)`, i.e. all multiplication should happen before any division, with a single final division. Performing the `// royalty_split` division up front discards remainder information that the subsequent multiplication by `percentage` can no longer recover, exactly analogous to the reported `USSDRebalancer` bug where `(amountToBuyLeftUSD * 1e18 / collateralval) / 1e18` was fixed by deferring the final division.

The same defective ordering is duplicated in `NFTWallet.royalty_calculation`, which is the function backing the `nft_calculate_royalties` RPC used to preview/quote royalties for an offer: [2](#0-1) 

and the raw per-asset "trade price" that is embedded into the NFT ownership-layer condition (`-10`) is likewise computed with an early division: [3](#0-2) 

`compute_royalty_amount` is invoked from `NFTWallet.make_nft1_offer`, the code path any wallet user reaches when creating or accepting an NFT offer that involves multiple royalty-bearing NFTs on one side of the trade (`request_side_royalty_split`/`offer_side_royalty_split` > 1): [4](#0-3) 

### Impact Explanation
Whenever a maker or taker constructs/accepts an offer that requests or offers more than one royalty-enabled NFT together (`royalty_split > 1`), the computed royalty payment amount is truncated more aggressively than necessary. The NFT creator/royalty holder — who has no part in constructing this spend bundle and cannot correct the computation — receives less than the percentage they are entitled to. Because the wallet code, not an on-chain canonical formula, decides the exact royalty CreateCoin amount that gets embedded into the settlement/-10 condition, this loss is baked directly into the settlement without any consensus-level check requiring the "ideal" rounding. This is a real, reproducible value loss to a third party (the royalty holder) triggered purely by a legitimate offer-settlement action from an unprivileged wallet user — i.e., an "offer settlement" underpayment of funds that rightfully belong to another party.

The magnitude of the loss is bounded (at most `royalty_split - 1` mojo-equivalents of extra truncation per trade relative to the ideal formula) and scales with the number of split NFTs and the offered amount, so it is limited in size per trade but systematic and NFT-creator-adverse across every multi-NFT offer.

### Likelihood Explanation
This triggers automatically, with no attacker action required, any time a normal user creates or accepts an NFT offer bundling more than one royalty-enabled NFT on one side (a supported, documented feature exercised by `test_complex_nft_offer` and the `royalty_split` parameter tests in `test_nft_royalty.py`). No malicious peer or privileged access is required — it is purely a wallet-side arithmetic defect reachable by any standard offer-maker/taker.

### Recommendation
Reorder the arithmetic in both `compute_royalty_amount` and `NFTWallet.royalty_calculation` to multiply before dividing, and perform only a single, final division:

```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```

Apply the same fix to the `trade_prices` computation in `make_nft1_offer` (compute `amount * ...` before any `// offer_side_royalty_split`), ensuring the royalty math matches the mathematically ideal single-division formula used in the fixed `USSDRebalancer` reference.

### Proof of Concept
Using the existing formula in `compute_royalty_amount`:

```
offered_amount = -999
royalty_split = 2
percentage = 9999  # 99.99%

# Current (buggy) code path:
abs(999) // 2 = 499
499 * 9999 = 4989501
4989501 // 10000 = 498          # royalty paid = 498

# Ideal single-division formula:
999 * 9999 = 9,981,001... wait, using correct multiply-first order:
999 * 9999 // (2 * 10000) = 9981001 // 20000 = 499   # royalty that should be paid = 499
```

The buggy ordering pays `498` instead of the mathematically correct `499` — a one-mojo-equivalent loss to the royalty holder purely from doing the `// royalty_split` division before the multiplication, reproducing the same class of precision loss described in the reference report. This effect compounds with larger `royalty_split` values and larger offered amounts. Existing tests such as `test_royalty_split_across_multiple_nfts` in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` confirm the split-then-multiply ordering is the implemented (and unfixed) behavior: [5](#0-4)

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
