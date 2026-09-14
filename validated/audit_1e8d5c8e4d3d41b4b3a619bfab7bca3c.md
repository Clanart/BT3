## Analog Found

### Title
Unchecked signed-to-unsigned conversion when computing mempool fees can crash spend bundle validation - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle` computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` without first verifying that `addition_amount <= removal_amount`. `addition_amount` and `removal_amount` are both plain Python `int` accumulators built directly from spend conditions and coin amounts, so nothing prevents `addition_amount` from exceeding `removal_amount` (i.e. an attempted coin-inflation spend). When that happens the subtraction is negative and the `uint64()` constructor raises an uncaught `OverflowError`, exactly mirroring the `Vault.vy` `int256`→`uint256` conversion bug: a signed arithmetic result that can legitimately be negative is blindly cast to an unsigned type.

### Finding Description
`validate_spend_bundle` accumulates `addition_amount` from every `CREATE_COIN` condition across all coin spends in the bundle [1](#0-0) , and accumulates `removal_amount` from the amounts of the coins actually being spent [2](#0-1) . Immediately afterward it computes:

```
fees = uint64(removal_amount - addition_amount)
``` [3](#0-2) 

There is no prior check in this function that `addition_amount <= removal_amount` (the check that exists for full block validation, `Err.MINTING_COIN`, lives only in `chia/consensus/block_body_validation.py` and is never invoked from the mempool path) [4](#0-3) . If an attacker crafts a spend bundle whose `CREATE_COIN` outputs sum to more than the sum of the coins it spends (an inflation/minting attempt), `addition_amount > removal_amount`, the subtraction is negative, and `uint64(negative_value)` raises `OverflowError` — the exact same class of "can't convert negative int to unsigned" failure demonstrated for `Coin` construction in this codebase's own test suite [5](#0-4) .

Crucially, `validate_spend_bundle` and its caller `add_spend_bundle` contain no `try/except` around this arithmetic [6](#0-5) , so the exception is not converted into a normal `Err`/`MempoolInclusionStatus.FAILED` result the way other invalid-spend conditions are (e.g. `Err.BLOCK_COST_EXCEEDS_MAX`, `Err.INVALID_BLOCK_FEE_AMOUNT`) [7](#0-6) . Instead of gracefully rejecting the malicious/malformed spend bundle, this code path raises an unhandled exception during mempool processing of an untrusted, attacker-supplied spend bundle.

This directly parallels the Vyper report: both cases perform a subtraction (`removal - addition` here, "debt" calculations there) whose sign is not validated before an unchecked signed→unsigned conversion, and both can be triggered by ordinary external input (a spend bundle here, an oracle/debt update there) rather than any privileged action.

### Impact Explanation
Any unprivileged spend-bundle submitter can construct a spend that attempts to create more value than it consumes (i.e., an invalid/minting spend that should simply be rejected with a specific `Err`). Because the fee computation crashes with an uncaught `OverflowError` before normal rejection logic runs, this turns what should be an ordinary "reject invalid spend" path into an unhandled exception inside mempool validation — a spend-triggered transaction-processing disruption rather than a clean `MempoolInclusionStatus.FAILED` response. This matches the "spend-triggered transaction-processing halt" impact category called out in scope.

### Likelihood Explanation
Likelihood is high in terms of reachability: any party able to submit a spend bundle to a node's mempool (via RPC or peer protocol) can trigger this code path by submitting a spend whose additions exceed its removals. No special privileges, signatures matching real coins with sufficient value, or state manipulation are required beyond constructing a spend bundle with mismatched CREATE_COIN totals; whether the CLVM execution or upstream checks (`spend_conds`) would reject such a bundle before reaching this line was not fully verifiable within the available context, so likelihood should be treated as tentative pending confirmation that no earlier guard exists between condition execution and this fee computation.

### Recommendation
Before computing `fees`, explicitly check `addition_amount > removal_amount` and return a proper `Err` (analogous to `Err.MINTING_COIN` in block body validation) instead of letting `uint64()` raise. Alternatively, wrap the conversion in a try/except and map any `OverflowError`/`ValueError` to `Err.MINTING_COIN` or `Err.UNKNOWN`, ensuring the mempool manager always responds with a well-formed `MempoolInclusionStatus.FAILED` result rather than propagating a raw exception from untrusted input.

### Proof of Concept
1. Construct a `SpendBundle` spending a single owned coin of amount `X`.
2. In its solution, emit a `CREATE_COIN` condition (or multiple) whose total output amount is `> X` (an inflation attempt) while all signatures are otherwise valid for the input coin.
3. Submit the bundle to the node's mempool (e.g., via `push_tx` RPC or peer gossip).
4. In `MempoolManager.validate_spend_bundle`, `addition_amount` (sum of `CREATE_COIN` amounts) exceeds `removal_amount` (sum of spent coin amounts), causing `fees = uint64(removal_amount - addition_amount)` at `chia/full_node/mempool_manager.py:814` to raise `OverflowError` instead of returning a controlled `Err`, as confirmed by the identical negative-to-`uint64` failure mode demonstrated in `chia/_tests/core/custom_types/test_coin.py:81-83`.

### Citations

**File:** chia/full_node/mempool_manager.py (L640-668)
```python
        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
        if err is None:
            # No error, immediately add to mempool, after removing conflicting TXs.
            assert item is not None
            conflict = self.mempool.remove_from_pool(remove_items, MempoolRemoveReason.CONFLICT)
            info = self.mempool.add_to_pool(item)
            if info.error is not None:
                return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.FAILED, [], info.error)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.SUCCESS, [*info.removals, conflict], None)
        elif err is Err.MEMPOOL_CONFLICT and item is not None:
            # The transaction has a conflict with another item in the
            # mempool, put it aside and re-try it later
            self._conflict_cache.add(item)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.PENDING, [], err)
        elif item is not None:
            # The transasction has a height assertion and is not yet valid.
            # remember it to try it again later
            self._pending_cache.add(item)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.PENDING, [], err)
        else:
            # Cannot add to the mempool or pending pool.
            return SpendBundleAddInfo(None, MempoolInclusionStatus.FAILED, [], err)
```

**File:** chia/full_node/mempool_manager.py (L748-753)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount
```

**File:** chia/full_node/mempool_manager.py (L784-812)
```python
        removal_record_dict: dict[bytes32, CoinRecord] = {}
        removal_amount: int = 0
        removal_records = await get_coin_records(removal_names)
        for record in removal_records:
            removal_record_dict[record.coin.name()] = record

        for name in removal_names:
            if name not in removal_record_dict and name not in additions_dict:
                return Err.UNKNOWN_UNSPENT, None, []
            if name in additions_dict:
                removal_coin = additions_dict[name]
                # The timestamp and block-height of this coin being spent needs
                # to be consistent with what we use to check time-lock
                # conditions (below). All spends (including ephemeral coins) are
                # spent simultaneously. Ephemeral coins with an
                # ASSERT_SECONDS_RELATIVE 0 condition are still OK to spend in
                # the same block.
                assert self.peak.timestamp is not None
                removal_record = CoinRecord(
                    removal_coin,
                    uint32(self.peak.height + 1),
                    uint32(0),
                    False,
                    self.peak.timestamp,
                )
                removal_record_dict[name] = removal_record
            else:
                removal_record = removal_record_dict[name]
            removal_amount += removal_record.coin.amount
```

**File:** chia/full_node/mempool_manager.py (L814-814)
```python
        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/full_node/mempool_manager.py (L816-827)
```python
        if cost == 0:
            return Err.UNKNOWN, None, []

        if cost > self.max_tx_clvm_cost:
            return Err.BLOCK_COST_EXCEEDS_MAX, None, []

        # this is not very likely to happen, but it's here to ensure SQLite
        # never runs out of precision in its computation of fees.
        # sqlite's integers are signed int64, so the max value they can
        # represent is 2^63-1
        if fees > MEMPOOL_ITEM_FEE_LIMIT or SQLITE_INT_MAX - self.mempool.total_mempool_fees() <= fees:
            return Err.INVALID_BLOCK_FEE_AMOUNT, None, []
```

**File:** chia/consensus/block_body_validation.py (L541-543)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN
```

**File:** chia/_tests/core/custom_types/test_coin.py (L81-83)
```python
    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```
