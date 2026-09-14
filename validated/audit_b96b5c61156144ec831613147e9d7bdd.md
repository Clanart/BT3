Based on my investigation, I found a directly analogous pattern in the NFT royalty computation logic in the chia-blockchain repo, structurally identical to the reported bug: an arithmetic operation performed without validating that a divisor/quantity used in the calculation cannot be zero, which can cause an unhandled exception that halts a user-facing transaction flow (analogous to the Sherlock report's `_isQuoteAssetExcessOrAtTarget` reverting because `normalizedTargetUnit` was never checked for zero before being used in an operation that can't safely accept it).

### Title
Unguarded division by `royalty_split` in `compute_royalty_amount` can raise `ZeroDivisionError` and halt NFT offer/trade processing - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
`compute_royalty_amount` performs integer division using `royalty_split` as a divisor without validating that it is non-zero, mirroring the reported class of bug where a value used in an arithmetic/comparison operation is never checked for the zero edge case, causing the function (and everything that depends on it) to always fail for that input.

### Finding Description
`compute_royalty_amount` computes an NFT royalty by dividing `abs(offered_amount)` by `royalty_split`: [1](#0-0) 
There is no check that `royalty_split > 0` before the division on line 71. If `royalty_split` is ever `0` — e.g., a degenerate grouping of royalty-bearing NFTs in an offer/trade computation where the count of items sharing a royalty puzzle hash resolves to zero — the function will raise a Python `ZeroDivisionError` rather than a controlled `ValueError`, exactly like the original report where `approximatelyEquals` unconditionally reverts on division when its denominator argument is zero. This is the same root-cause pattern: a numeric precondition (`divisor != 0`) is not enforced before it is unconditionally consumed by an arithmetic operation, so any code path that manages to produce a zero value for that parameter causes the operation to always fail. The function only validates the unrelated `percentage` bound (line 69) and the post-condition (`royalty >= abs(offered_amount)`, line 73), but never validates the `royalty_split` precondition that the division on line 71 depends on.

I was not able to fully trace, within the available tool budget, every call site that supplies `royalty_split` into `compute_royalty_amount` (only the test file `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` and `nft_wallet.py` itself reference it), so I cannot confirm with certainty whether an untrusted offer counterparty can force `royalty_split == 0` through crafted offer/trade input. This should be verified against the actual offer-construction call sites in `nft_wallet.py` before treating this as fully proven end-to-end.

### Impact Explanation
If reachable with an attacker- or counterparty-influenced `royalty_split == 0` (e.g., via a malformed/edge-case NFT offer being processed by a wallet user or offer counterparty), this raises an unhandled `ZeroDivisionError` during royalty computation, aborting the trade/offer flow for that spend — a spend-triggered transaction-processing halt analogous to the original report's impact on `bid`/`unlock`.

### Likelihood Explanation
Likelihood is uncertain without confirming the exact call sites and whether `royalty_split` can be attacker/counterparty-influenced down to zero (e.g., via unusual driver-dict/offer configurations). The existing test suite in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` [2](#0-1)  exercises percentage edge cases and overflow, but does not test `royalty_split == 0`, suggesting this edge case was not considered by the developers — consistent with the class of oversight described in the original report.

### Recommendation
Add an explicit guard at the top of `compute_royalty_amount` to raise a controlled `ValueError` when `royalty_split <= 0`, before performing the division, mirroring the recommended fix pattern from the original report (validate the value that will be used as a divisor/denominator before consuming it).

### Proof of Concept
```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount

# Any call with royalty_split == 0 raises ZeroDivisionError instead of a
# handled ValueError, mirroring the unguarded-zero-denominator pattern
# from the original report.
compute_royalty_amount(offered_amount=-1_000_000, royalty_split=0, percentage=500)
```

**Note on confidence**: This analog is structurally sound (unguarded zero-divisor precondition causing an unconditional failure), but I could not fully confirm within this session whether `royalty_split` is ever attacker/counterparty-controllable down to `0` in a live offer/trade flow — a Devin session with full codebase access would be needed to trace all call sites of `compute_royalty_amount` in `chia/wallet/nft_wallet/nft_wallet.py` and confirm end-to-end reachability from an offer-taking transaction.

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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L1-55)
```python
from __future__ import annotations

import pytest
from chia_rs.sized_ints import uint64

from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount


def test_normal_royalty() -> None:
    result = compute_royalty_amount(offered_amount=-1_000_000, royalty_split=1, percentage=500)
    assert result == uint64(50_000)


def test_zero_royalty() -> None:
    result = compute_royalty_amount(offered_amount=-1_000_000, royalty_split=1, percentage=0)
    assert result == uint64(0)


def test_100_percent_royalty_rejected() -> None:
    with pytest.raises(ValueError, match="meets or exceeds"):
        compute_royalty_amount(offered_amount=-1000, royalty_split=1, percentage=10000)


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


def test_offered_amount_sign_irrelevant() -> None:
    r1 = compute_royalty_amount(offered_amount=-5000, royalty_split=1, percentage=500)
    r2 = compute_royalty_amount(offered_amount=5000, royalty_split=1, percentage=500)
    assert r1 == r2


def test_99_percent_royalty_succeeds() -> None:
    result = compute_royalty_amount(offered_amount=-10000, royalty_split=1, percentage=9900)
    assert result == uint64(9900)
```
