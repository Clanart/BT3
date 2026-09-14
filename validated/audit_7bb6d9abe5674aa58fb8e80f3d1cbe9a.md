## Analog Found

### Title
NFT Royalty Payments Can Be Rounded Down to Zero via Multi-NFT Offer Bundling, Diverting Rightful Creator Payments - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The external report describes a class of bug where a threshold/rounding rule used to classify a payment (withdrawal vs. reward) can be gamed by an adversarial party to redirect funds away from the party who is rightfully owed them. The closest reachable analog in this codebase is in Chia's NFT royalty computation for offers: `compute_royalty_amount()` uses floor (integer) division split across the number of royalty-enabled NFTs on a side of the trade, and any offer/trade counterparty can choose how many NFTs to bundle into a single offer. By increasing the "split" divisor (bundling many low-value or low-royalty-percentage NFTs together), a counterparty can drive individual, and in aggregate, the total royalty payment down to zero — even though a single equivalent-value trade would owe a nonzero royalty to the NFT creator.

### Finding Description
`compute_royalty_amount()` computes the per-asset royalty using truncating integer arithmetic: [1](#0-0) 

This is invoked from `NFTWallet.make_nft1_offer()`, where `request_side_royalty_split`/`offer_side_royalty_split` are simply the *count* of royalty-enabled NFTs on the requesting/offering side of a trade, fully controlled by whichever party constructs the offer: [2](#0-1) [3](#0-2) 

Because `amount` is divided by `royalty_split` *before* multiplying by `percentage` and dividing by `MAX_ROYALTY_BASIS_POINTS` (10000), each additional NFT bundled into the trade increases the divisor and shrinks each per-NFT royalty computation. The project's own tests confirm this rounds fully to zero for small enough per-item amounts: [4](#0-3) 

An offer maker or taker who controls the composition of an offer (how many royalty-bearing NFTs are bundled together, and their relative trade prices) can therefore deliberately structure the trade to zero out or minimize royalty obligations that would otherwise be owed to NFT creators, analogous to how OWR's binary "reward vs. withdrawal" threshold could be gamed by a party that controls the underlying event (slashing) to move funds it isn't entitled to.

### Impact Explanation
NFT creators configure royalty percentages expecting a proportional payment on every qualifying trade. An offer counterparty can reduce or entirely eliminate the royalty payment owed to the creator by bundling multiple NFTs/low trade-price legs into a single offer, exploiting the floor-division rounding. This is a value-diversion issue (funds that should go to the royalty recipient instead stay with the trade counterparty), fitting the "offer settlement theft" impact category, though the amount divertible per transaction is bounded by the number of items bundled and the royalty percentage/trade price involved.

### Likelihood Explanation
Any wallet user constructing or responding to an NFT offer has full control over how many NFTs and fungible legs are bundled together and can freely choose these to trigger the rounding-to-zero behavior; no privileged access or malicious node/peer behavior is required, only ordinary offer construction using the standard RPC/wallet flow (`nft_calculate_royalties`, offer creation). This makes exploitation straightforward and repeatable for any user seeking to avoid paying royalties in multi-item trades.

### Recommendation
Compute royalty on the aggregate trade value first and then distribute proportionally (e.g., using largest-remainder or ceiling-based distribution) rather than doing a floor division per split before applying the percentage, so that the sum of individual royalty payments cannot systematically round down to less than the royalty owed on the full trade value. Alternatively, enforce a minimum-royalty check that rejects trades where the aggregate computed royalty is disproportionately smaller than `total_trade_value * percentage / 10000`.

### Proof of Concept
1. An NFT creator sets a 1% (100 basis point) royalty on an NFT collection.
2. A buyer constructs an offer requesting many NFTs from that collection simultaneously (or splits payment legs finely) in exchange for a fungible asset, so that `request_side_royalty_split` is large relative to the requested amount for each fungible leg.
3. `compute_royalty_amount(amount, request_side_royalty_split, percentage)` computes `abs(amount) // royalty_split * percentage // 10000` for each NFT; with a sufficiently large split relative to `amount`, each per-NFT royalty truncates to 0, as demonstrated by `test_small_amount_truncates_to_zero` (`chia/_tests/wallet/nft_wallet/test_nft_royalty.py:42-44`).
4. The offer settles with royalty payments of 0 mojos to the creator's `royalty_address`, despite the aggregate trade value being large enough that a single-NFT trade of the same total value would have paid a nonzero royalty.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L887-902)
```python
        # Let's gather some information about the royalties
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
