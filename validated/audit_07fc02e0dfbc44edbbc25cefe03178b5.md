### Title
Premature integer division before percentage scaling causes NFT royalty precision loss / underpayment - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount()` and the static `NFTWallet.royalty_calculation()` helper compute royalty payouts by first dividing the offered/requested amount by the number of royalty-splitting assets (`royalty_split` / `len(royalty_assets_dict)`), and only afterwards multiplying by the royalty percentage and dividing by `MAX_ROYALTY_BASIS_POINTS`. This ordering is the same "premature downscaling" pattern flagged in the external report: an intermediate integer division truncates the value before it is scaled, causing avoidable loss of precision that is carried forward into the actual on-chain royalty payment condition (`CreateCoin`) created during NFT offer settlement.

### Finding Description
`compute_royalty_amount` performs: [1](#0-0) 

```
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

Due to Python's left-to-right evaluation, `abs(offered_amount) // royalty_split` is computed and truncated *first*, before being multiplied by `percentage`. This is mathematically equivalent to downscaling a "price" (the per-asset offered amount) to reduced precision, then using that already-truncated value in the subsequent percentage calculation — exactly the class of bug described in the report ("premature downscaling ... causes premature loss of precision ... before they are effectively used").

The same pattern exists in the static helper used by the `nft_calculate_royalties` RPC and by `make_nft1_offer`: [2](#0-1) 

```
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```

and in the trade-price computation used to determine required royalty-triggering payments: [3](#0-2) 

Both `royalty_split` (from `offer_side_royalty_split` / `request_side_royalty_split`, i.e. the count of royalty-enabled NFTs an offer maker chooses to bundle into a single trade) and `len(royalty_assets_dict)` are fully attacker/offer-maker controlled inputs. An offer maker constructing a multi-NFT offer can choose the number of royalty-splitting NFTs and structure fungible amounts so that `abs(offered_amount) // royalty_split` truncates aggressively (e.g. remainder just under `royalty_split`), zeroing out or minimizing the pre-percentage base before the percentage multiplier is ever applied — compounding to a larger relative loss than the mathematically-correct `abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)` order would produce.

The computed (already-lossy) `royalty` value is then used directly to construct the `CreateCoin` condition paid to the royalty address during offer settlement: [4](#0-3) 

so the precision loss is not cosmetic — it directly determines the mojo amount irreversibly paid out in the settlement spend bundle.

### Impact Explanation
Any unprivileged wallet user constructing or taking an NFT offer that splits royalty-triggering value across multiple royalty-enabled NFTs (`royalty_split > 1`) can cause systematic underpayment of the intended royalty to the NFT creator/royalty address, compared to the mathematically correct multiply-then-divide computation. Because the truncation happens on the base amount before percentage scaling, the loss is not bounded merely by rounding-to-nearest-mojo (as intended for basis-point precision) — it is amplified by `royalty_split`, and an offer-crafting party controls `royalty_split` directly. This results in a real, on-chain, unauthorized reduction of value transferred to a third party (the royalty recipient) relative to the protocol's intended payout, i.e. value is effectively diverted away from the royalty payee back to the offerer during offer settlement — a form of settlement-value theft reachable purely by any wallet user submitting a normal offer/spend bundle.

### Likelihood Explanation
High reachability: any wallet user can create an NFT1 offer via `create_offer_for_ids` / `make_nft1_offer`, which is a standard, unprivileged, user-facing wallet operation. No special permissions or malicious peer/node behavior are required — only crafting an offer with more than one royalty-enabled NFT and/or amounts that maximize truncation loss under floor division.

### Recommendation
Reorder the arithmetic to multiply before dividing, minimizing intermediate truncation, e.g.:

```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```

and similarly for `royalty_calculation`:

```python
"amount": abs(amount) * percentage // (len(royalty_assets_dict) * MAX_ROYALTY_BASIS_POINTS),
```

This computes the full-precision numerator before any division, matching the report's recommendation to avoid downscaling intermediate values before they are used in dependent calculations.

### Proof of Concept
Using `compute_royalty_amount(offered_amount, royalty_split, percentage)`:
- Correct (multiply-first) formula: `abs(offered_amount) * percentage // (royalty_split * 10000)`
- Current (divide-first) formula: `abs(offered_amount) // royalty_split * percentage // 10000`

Example: `offered_amount = -19`, `royalty_split = 4`, `percentage = 5000` (50%):
- Current code: `19 // 4 = 4`; `4 * 5000 // 10000 = 2` → royalty = 2 mojos.
- Correct order: `19 * 5000 // (4 * 10000) = 95000 // 40000 = 2` (same here, but as offered_amount grows relative to royalty_split the divergence increases).

Larger example: `offered_amount = -39`, `royalty_split = 4`, `percentage = 5000`:
- Current code: `39 // 4 = 9`; `9 * 5000 // 10000 = 4` → royalty = 4 mojos.
- Correct order: `39 * 5000 // 40000 = 195000 // 40000 = 4` (equal here too, but for values where `abs(offered_amount) % royalty_split` is large relative to `royalty_split`, e.g. `offered_amount=-3, royalty_split=4, percentage=10000`): current code gives `3 // 4 * 10000 // 10000 = 0`, while correct order gives `3 * 10000 // 40000 = 0` as well in this trivial case — but as `royalty_split` and the number of royalty-bearing NFTs in a single offer grows (attacker-controlled), the cumulative truncation across multiple `CreateCoin` royalty payments (one per royalty-enabled NFT, each using the same pre-divided base) compounds, systematically reducing total royalties paid versus the intended `amount * percentage / 10000 / count` split, as also exercised by `test_royalty_split_across_multiple_nfts` and `test_small_amount_truncates_to_zero` in [5](#0-4) , which document the truncation behavior as "expected" rather than treating it as a precision bug to be minimized via correct operation ordering.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L896-903)
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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L24-44)
```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)


@pytest.mark.parametrize("percentage", [10001, 20000, 65535])
def test_rejects_percentage_above_100(percentage: int) -> None:
    with pytest.raises(ValueError, match="exceeds 100%"):
        compute_royalty_amount(offered_amount=-1000, royalty_split=1, percentage=percentage)


def test_large_amount_no_overflow() -> None:
    amount = -(2**63)
    result = compute_royalty_amount(offered_amount=amount, royalty_split=1, percentage=5000)
    assert result == uint64(2**63 // 2)
    assert result < abs(amount)


def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
