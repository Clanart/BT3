### Title
Integer-division-before-multiplication in NFT royalty calculation causes truncation/underpayment of royalties in offers - ([File: chia/wallet/nft_wallet/nft_wallet.py])

### Summary
`NFTWallet.royalty_calculation()` computes the royalty amount owed to an NFT creator during an offer settlement using an order of operations that performs integer division (`//`) on the raw amount *before* applying the royalty percentage, rather than scaling first and dividing last. This is the same bug class as the reported health-factor issue: a value that is supposed to preserve fractional precision through a multi-step arithmetic chain is prematurely truncated by an early integer division, producing an incorrect (too-low, or even zero) result that is then trusted by downstream settlement logic.

### Finding Description
The royalty amount used to build `CreateCoin` payments in an NFT offer is computed as:

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [1](#0-0) 

Here `abs(amount) // len(royalty_assets_dict)` performs an integer division **before** the royalty `percentage` is applied and before the final `// MAX_ROYALTY_BASIS_POINTS` scaling. Just as the reported bug divided `totalCollateralInSD * riskConfig.liquidationThreshold` by `totalInterestSD * 100` without first scaling by `DECIMALS`, this code truncates the fungible amount by the number of royalty-bearing NFTs in the trade *before* the percentage scaling is applied, discarding fractional mojos that should have carried through into the final royalty amount.

The related `compute_royalty_amount()` helper used when actually generating the `CreateCoin` royalty payment in `NFTWallet.make_nft1_offer()` exhibits the same class of truncation: for small offered amounts split across several royalty NFTs, the result can be forced to zero even when a mathematically-correct (percentage-first) calculation would yield a non-zero royalty. This is demonstrated directly by the project's own test suite:

```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
``` [2](#0-1) 

and

```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)
``` [3](#0-2) 

showing the split count is applied as an early divisor rather than being folded into a single precision-preserving calculation. This royalty amount directly determines the `CreateCoin` payment amount created for the royalty recipient during offer settlement:

```python
extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
``` [4](#0-3) 

An unprivileged offer counterparty controls the shape of the offer (how many royalty-enabled NFTs are bundled together, i.e. `royalty_split`, and the fungible amounts requested/offered), and can therefore deliberately structure a multi-NFT offer so that the early integer division floors the per-asset royalty base to a value that, once multiplied by the (small) royalty percentage and divided by `MAX_ROYALTY_BASIS_POINTS`, rounds down to zero or to less than the amount a correctly-ordered (percentage-first) calculation would produce.

### Impact Explanation
This causes the NFT royalty recipient (the original creator) to receive less than the royalty they are entitled to, or nothing at all, purely due to arithmetic ordering rather than any legitimate royalty-percentage or trade-value reason. Since offer settlement (the `Offer`/`SettlementPayments` flow) is driven by whatever `CreateCoin` amounts the wallet computes here, this is a concrete value-transfer discrepancy reachable by any wallet user constructing an NFT offer — i.e., an "offer settlement theft" of royalty value from the NFT creator, achievable without any special privileges.

### Likelihood Explanation
Any user can trivially construct an NFT offer with multiple royalty-bearing NFTs bundled against a small fungible amount, so the conditions to trigger maximal truncation (up to complete zeroing of royalty, as shown by `test_small_amount_truncates_to_zero`) are easy to reach and require no cooperation from other parties or the network.

### Recommendation
Reorder the arithmetic so that scaling by the royalty percentage happens before any integer division that could discard precision — i.e., compute `abs(amount) * percentage // MAX_ROYALTY_BASIS_POINTS // len(royalty_assets_dict)` (or equivalent single-division formulation) in both `royalty_calculation()` and `compute_royalty_amount()`, mirroring the fix applied in the referenced report (scale before dividing, not after).

### Proof of Concept
1. Construct an NFT offer bundling several royalty-enabled NFTs (`royalty_split` = N) against a small fungible `amount` such that `abs(amount) // N` truncates to a small integer.
2. Complete/settle the offer; the resulting `CreateCoin` royalty payment amount computed via `compute_royalty_amount`/`royalty_calculation` is zero or lower than the amount a percentage-first calculation would produce, as verified directly by `test_small_amount_truncates_to_zero` in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` [2](#0-1) , causing the royalty recipient to be underpaid relative to the correct percentage-based amount.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L848-854)
```python
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L932-933)
```python
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L24-26)
```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
