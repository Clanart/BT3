### Title
Unvalidated minting spend bundle causes unhandled `OverflowError` in mempool fee calculation - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager`'s spend-bundle validation path computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount` (i.e., that the spend bundle does not mint value). This mirrors the reported Curve/Vyper bug where `new_total_value` was assumed non-negative before being converted to `uint256`: here `removal_amount - addition_amount` is assumed non-negative before being converted to `uint64`, and the assumption is not actually enforced at this point in the code.

### Finding Description
In `MempoolManager` (relevant excerpt below), `addition_amount` is accumulated from the `create_coin` conditions of every coin spend in the bundle, and `removal_amount` is accumulated from the amounts of the coins being spent: [1](#0-0) [2](#0-1) 

At line 814, the difference is directly cast to `uint64`:
```python
fees = uint64(removal_amount - addition_amount)
```
There is no prior check in this function (unlike the block-level consensus check `Err.MINTING_COIN` in `chia/consensus/block_body_validation.py`) that `removal_amount >= addition_amount`. `chia_rs`'s `uint64` type raises `OverflowError: can't convert negative int to unsigned` when given a negative Python int, exactly as demonstrated in the repo's own tests for `Coin` construction: [3](#0-2) 

Block-level validation does perform this check correctly, guarding against minting, before any fee-like arithmetic is done: [4](#0-3) 

But `MempoolManager.validate_spend_bundle`/`add_spend_bundle` (the code path exercised the moment any unprivileged peer or wallet RPC caller submits a spend bundle for mempool admission) does not replicate that ordering — it computes the `uint64` fee before validating that the bundle is not minting coins. Any spend bundle whose `create_coin` conditions sum to more than the amount of the coins it spends (a purely local, structurally invalid but easily constructible spend bundle — no attacker capability beyond crafting CLVM conditions is required) will cause `addition_amount > removal_amount`, making `removal_amount - addition_amount` negative and raising an uncaught `OverflowError` inside the mempool validation coroutine.

### Impact Explanation
An uncaught `OverflowError` raised deep inside mempool spend-bundle validation is a processing exception rather than a graceful `Err.MINTING_COIN`-style rejection. Depending on how far up the call stack this exception propagates before being caught, this can disrupt the full node's mempool-manager task handling that specific request/peer message, i.e., a spend-bundle-triggered transaction-processing disruption reachable by any unprivileged submitter — matching the "spend-triggered transaction-processing halt" impact category. It does not itself inflate supply or forge assets because a bundle with unbalanced inputs/outputs would still ultimately fail the AGG_SIG/CLVM balance requirements enforced elsewhere, but the failure mode here is an unhandled exception rather than a clean rejection path, which is the core defect pattern flagged in the source report (an assumed-non-negative value fed straight into an unsigned-integer constructor without a guard).

### Likelihood Explanation
Likelihood is high in terms of triggerability (any caller can submit a spend bundle whose `create_coin` total exceeds its input coin total — this requires no signature validity, no special coin, and no network position), but the resulting severity depends on whether an uncaught exception here actually crashes/blocks the mempool manager loop or is swallowed by an outer exception handler that I could not fully trace given the available file context.

### Recommendation
Perform the minting check (`if addition_amount > removal_amount: return Err.MINTING_COIN, None, []`) in `chia/full_node/mempool_manager.py` before computing `fees = uint64(removal_amount - addition_amount)`, mirroring the ordering already used in `chia/consensus/block_body_validation.py` (`Err.MINTING_COIN` check strictly before the `fees = removed - added` subtraction). This avoids relying on downstream `uint64` conversion to implicitly enforce non-negativity and ensures spend bundles that attempt to mint value are rejected with a proper `Err` value instead of raising an unhandled `OverflowError`.

### Proof of Concept
1. Construct a spend bundle with one coin spend whose puzzle reveal/solution issues a single `CREATE_COIN` condition for an amount greater than the coin's own amount (no fee-generating balance elsewhere in the bundle).
2. Submit it via the wallet/RPC path that calls into `MempoolManager` spend-bundle validation.
3. `addition_amount` (sum of `create_coin` amounts) exceeds `removal_amount` (sum of spent coin amounts).
4. At `fees = uint64(removal_amount - addition_amount)` (`chia/full_node/mempool_manager.py:814`), Python raises `OverflowError: can't convert negative int to unsigned` (as validated by the existing negative-`uint64` test at `chia/_tests/core/custom_types/test_coin.py:81-83`), instead of the intended `Err.MINTING_COIN` rejection performed by the equivalent block-level check at `chia/consensus/block_body_validation.py:541-546`.

### Citations

**File:** chia/full_node/mempool_manager.py (L748-753)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount
```

**File:** chia/full_node/mempool_manager.py (L810-814)
```python
            else:
                removal_record = removal_record_dict[name]
            removal_amount += removal_record.coin.amount

        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/_tests/core/custom_types/test_coin.py (L81-83)
```python
    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```

**File:** chia/consensus/block_body_validation.py (L541-546)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```
