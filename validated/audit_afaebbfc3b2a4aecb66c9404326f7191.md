## Analysis

The reported bug class (division performed before multiplication, causing avoidable truncation/precision loss) has a direct analog in the wallet's NFT royalty computation.

`compute_royalty_amount` in `chia/wallet/nft_wallet/nft_wallet.py` computes the royalty owed to an NFT creator when royalty-bearing NFTs are offered/settled in a trade: [1](#0-0) 

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

This is called directly from `NFTWallet.make_nft1_offer` when constructing the actual `CreateCoin` payment to the royalty address: [2](#0-1) 

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

### Title
Premature integer division in `compute_royalty_amount` truncates royalty payments below the mathematically intended value - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount` computes `abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS`, dividing by `royalty_split` *before* multiplying by `percentage`. Because Python integers are arbitrary-precision, there is no overflow reason to divide first; doing so only discards precision that the final multiplication can no longer recover, systematically underpaying the NFT royalty recipient whenever `abs(offered_amount) % royalty_split != 0`.

### Finding Description
Floor-dividing first (`abs(offered_amount) // royalty_split`) truncates any fractional remainder from that division immediately. The subsequent `* percentage // MAX_ROYALTY_BASIS_POINTS` operates on the already-truncated value, so it can never recover the precision lost in the first division. Mathematically, `(a // b) * c // d <= (a * c) // b // d` for all non-negative integers, i.e. the current ordering can only underestimate (never overestimate) the correct royalty relative to performing the multiplication first and dividing last.

This function is invoked in `make_nft1_offer`, the code path used whenever any wallet user creates or takes an offer involving a royalty-enabled NFT (`chia offer` NFT flows), whenever more than one royalty-bearing NFT is on the same side of a trade (`request_side_royalty_split > 1`, which sets `royalty_split` accordingly) [3](#0-2) . This is a normal, unprivileged user action (any offer maker/taker), not an attacker-only or admin-only path.

### Impact Explanation
Every NFT royalty payment computed via this path is deterministically biased downward whenever `abs(offered_amount)` is not evenly divisible by `royalty_split` (i.e., whenever more than one royalty NFT shares the same fungible-asset payment). The royalty recipient (NFT creator/collection) is systematically underpaid relative to the percentage they configured, while the counterparty effectively retains the difference. Since this happens inside spend-bundle construction for real coin creation (`CreateCoin`), it directly affects on-chain fund distribution for every affected NFT offer settlement — a concrete value-leakage/underpayment issue in the offer settlement mechanism, not merely a display bug.

### Likelihood Explanation
High likelihood of occurrence: it triggers automatically, with no attacker action required, any time an offer bundles two or more royalty-enabled NFTs on the same side (a common and encouraged use case, e.g. "complex" multi-NFT offers exercised in `test_complex_nft_offer`). It is deterministic and reproducible, not probabilistic.

### Recommendation
Reorder the arithmetic to perform all multiplications before any division, and only floor-divide once at the very end:
```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
This preserves full precision through the computation and matches the intended royalty percentage as closely as integer arithmetic allows, without introducing overflow risk (Python integers are arbitrary precision).

### Proof of Concept
```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount

# offered_amount=-7, royalty_split=3 (three royalty NFTs on the taker side),
# percentage=9999 basis points (~99.99%)
current = compute_royalty_amount(offered_amount=-7, royalty_split=3, percentage=9999)
# current == 1  (7 // 3 = 2; 2 * 9999 = 19998; 19998 // 10000 = 1)

correct = abs(-7) * 9999 // (3 * 10000)
# correct == 2  (7 * 9999 = 69993; 69993 // 3 = 23331; 23331 // 10000 = 2)

assert current < correct  # royalty recipient is underpaid due to premature division
```

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
