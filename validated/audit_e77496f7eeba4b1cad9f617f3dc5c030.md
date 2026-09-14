### Title
Precision loss from division-before-multiplication in NFT royalty computation causes systematic royalty underpayment - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount()` computes an NFT royalty payment by dividing before multiplying, using two chained integer (floor) divisions. This truncates the intermediate result and systematically underpays the royalty recipient relative to the mathematically correct (multiply-then-divide) computation, mirroring the reported "division before multiplication" precision-loss bug class.

### Finding Description
`compute_royalty_amount` is defined as: [1](#0-0) 

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

This performs `abs(offered_amount) // royalty_split` (an integer division, truncating) **before** multiplying by `percentage`, and then divides again by `MAX_ROYALTY_BASIS_POINTS` (10000). Each `//` truncates toward zero, so performing division first discards fractional value that a correct `multiply-then-divide` ordering (`abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS`) would have preserved until the final division.

Concrete example demonstrating the discrepancy:
- `offered_amount = -999`, `royalty_split = 10`, `percentage = 9999` (99.99%)
- Current code: `999 // 10 = 99`; `99 * 9999 = 989901`; `989901 // 10000 = 98`
- Correct order: `999 * 9999 = 9989001`; `9989001 // 10 = 998900`; `998900 // 10000 = 99`
- Result: current code yields `98`, correct computation yields `99` — a systematic 1-unit (or more, scaling with amount) underpayment.

This function is invoked when constructing an NFT offer's royalty payment, which becomes a real `CreateCoin` amount sent to the royalty address: [2](#0-1) 

The sibling helper `royalty_calculation` (used by the `nft_calculate_royalties` RPC to preview/report expected royalty amounts) has the identical division-before-multiplication pattern: [3](#0-2) 

Because both the actual payment construction and the RPC-reported expectation use the same flawed ordering, the underpayment is self-consistent and not flagged by mismatch checks, but it still deviates from the intended `percentage` of `offered_amount` that the NFT's on-chain royalty configuration specifies.

### Impact Explanation
This is reachable by any offer maker/taker constructing or accepting an NFT offer with `royalty_split > 1` (multiple NFTs sharing a royalty pool) or with a `percentage`/`amount` combination that doesn't divide evenly. The royalty recipient (an NFT owner entitled to a percentage of trade value) receives a coin whose amount is silently smaller than the percentage they are contractually owed, while the counterparty effectively retains the truncated difference. This is directly analogous to the reported bug: a party entitled to a proportional fee receives systematically less than intended due to premature integer division, misaligning incentives between offer counterparties. It does not enable outright coin theft or supply inflation, and the amounts lost per truncation are small (bounded by `royalty_split - 1` units scaled through the percentage division), but it is a real, deterministic, and reproducible economic shortfall imposed on offer counterparties—consistent with a Medium-severity precision/accounting defect rather than a critical fund-safety break.

### Likelihood Explanation
High likelihood of triggering under normal usage: any complex NFT offer with `royalty_split > 1` (multiple royalty-bearing NFTs in one offer) or with a percentage value that does not evenly divide the offered amount will hit this path every time `make_nft1_offer` constructs royalty payments, with no attacker action required beyond normal offer creation.

### Recommendation
Reorder the arithmetic to multiply before dividing, minimizing truncation, e.g.:
```python
amount = abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS
```
Apply the same fix to the equivalent expression in `NFTWallet.royalty_calculation` so that the RPC-reported royalty summary matches the corrected payment computation.

### Proof of Concept
```python
def compute_royalty_amount_current(offered_amount, royalty_split, percentage):
    MAX_ROYALTY_BASIS_POINTS = 10000
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
    return amount

def compute_royalty_amount_correct(offered_amount, royalty_split, percentage):
    MAX_ROYALTY_BASIS_POINTS = 10000
    amount = abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS
    return amount

# offered_amount=-999, royalty_split=10 (10 NFTs sharing royalty pool), percentage=9999 (99.99%)
current = compute_royalty_amount_current(-999, 10, 9999)   # -> 98
correct = compute_royalty_amount_correct(-999, 10, 9999)   # -> 99

assert correct > current  # royalty recipient underpaid by at least 1 unit under current code
```
This mirrors the referenced Sherlock report's Foundry PoC pattern and confirms the wallet-level royalty computation used in `make_nft1_offer` (chia/wallet/nft_wallet/nft_wallet.py:932) systematically underpays royalty recipients relative to the mathematically correct multiply-then-divide ordering.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L839-856)
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
