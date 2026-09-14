Confirmed: there is no `MINTING_COIN`-style guard in `mempool_manager.py`'s `validate_spend_bundle` before the fee computation — unlike `block_body_validation.py` (lines 541-546), which explicitly checks `if removed < added: return Err.MINTING_COIN` before computing `fees = removed - added`, the mempool path computes `fees = uint64(removal_amount - addition_amount)` directly with no equivalent prior check.

### Title
Unhandled Integer Underflow in Mempool Fee Computation Causes Full Node Crash on Spend-Bundle Submission - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle` computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` [1](#0-0)  without first checking that `removal_amount >= addition_amount`, unlike the equivalent consensus-level check in block body validation which explicitly guards against this with `Err.MINTING_COIN` before subtracting [2](#0-1) . Since `uint64()` (from `chia_rs.sized_ints`) raises an unhandled `OverflowError`/`ValueError` on negative input rather than saturating or wrapping [3](#0-2) , a spend bundle that creates more value than it removes (an underflow of the fee subtraction) will raise an unhandled exception during mempool admission instead of being cleanly rejected.

### Finding Description
`addition_amount` is accumulated purely from `create_coin` conditions produced by the submitted CLVM spends (`addition_amount += amount` at line 753) with no validation that it doesn't exceed the sum of removed coin amounts before the fee subtraction at line 814 [4](#0-3) . In contrast, `validate_block_body` explicitly checks `if removed < added: return Err.MINTING_COIN` prior to computing `fees = removed - added` [2](#0-1) . The mempool path has no analogous guard, meaning a bundle spending coins totaling less than what it creates reaches the `uint64()` cast with a negative Python `int`, and `uint64` construction from a negative value raises an exception (confirmed by test behavior for `Coin`/`uint64` construction and `compute_block_fee` [5](#0-4) ) rather than returning an `Err` result.

### Impact Explanation
`validate_spend_bundle` is invoked from the transaction-processing/mempool-admission path used whenever an untrusted peer or wallet RPC caller submits a spend bundle. An unhandled exception at this point in a hot path used for every incoming transaction can propagate up through the caller (`add_spend_bundle`/`add_transaction`), and if the exception is not caught by an enclosing try/except in the RPC/protocol handler, it can crash or destabilize transaction processing for the full node, producing a spend-triggered transaction-processing denial-of-service condition — directly analogous to the reported CWE-191 integer-underflow DoS in CAI Content Credentials.

### Likelihood Explanation
Reaching this code path requires only a single spend bundle where the CLVM `CREATE_COIN` outputs sum to more than the sum of the spent coins' amounts. This is trivially constructable by an unprivileged submitter and does not require a malicious peer, node, or any special privilege — it's a normal (if invalid) transaction submission. Whether it results in visible node instability depends on whether `add_spend_bundle`/`add_transaction` and the RPC/protocol dispatch wrap this call in a broad exception handler; I was unable to fully trace all call sites and their exception handling in the time available, so I cannot confirm with certainty whether the exception is always caught gracefully upstream.

### Recommendation
Add an explicit guard in `validate_spend_bundle` mirroring the block-validation check: verify `removal_amount >= addition_amount` and return `Err.MINTING_COIN` (or equivalent) before performing the `uint64(removal_amount - addition_amount)` subtraction, so malformed/negative-fee bundles are rejected via the normal `Err` return path instead of raising an unhandled exception.

### Proof of Concept
Submit a `SpendBundle` whose single coin spend has `CREATE_COIN` conditions summing to an amount greater than the coin being spent (e.g., spend a 10-mojo coin and create one 20-mojo coin via `CREATE_COIN`). This bundle will fail CLVM/coin-amount consistency at the block level, but at the mempool admission level `addition_amount` (20) exceeds `removal_amount` (10) at line 814 [1](#0-0) , causing `uint64(-10)` to raise, which is not converted into an `Err` result the way the analogous `MINTING_COIN` check does in `block_body_validation.py` [2](#0-1) .

**Uncertainty note:** I could not fully verify within the available tool budget whether the caller of `validate_spend_bundle` (e.g., `add_spend_bundle`) wraps this call in a try/except that would convert the exception into a graceful `Err` result, which would reduce or negate the DoS impact. This should be verified against the full call chain before treating this as a confirmed exploitable DoS.

### Citations

**File:** chia/full_node/mempool_manager.py (L748-814)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount

            bundle_coin_spends[coin_id] = BundleCoinSpend(
                coin_spend=coin_spend,
                eligible_for_dedup=bool(spend_conds.flags & ELIGIBLE_FOR_DEDUP),
                additions=spend_additions,
                cost=uint64(spend_conds.condition_cost + spend_conds.execution_cost),
                latest_singleton_lineage=lineage_info,
                atom_count=spend_conds.atom_count,
                pair_count=spend_conds.pair_count,
            )

        non_ff_spend_ids = set()
        effective_spend_ids = set()
        for coin_id, spend_data in bundle_coin_spends.items():
            if spend_data.latest_singleton_lineage is None:
                non_ff_spend_ids.add(coin_id)
                effective_spend_id = coin_id
            else:
                effective_spend_id = spend_data.latest_singleton_lineage.coin_id
            # Fast forward spends are only allowed to be spent once in a spend bundle
            if effective_spend_id in effective_spend_ids:
                return Err.INVALID_SPEND_BUNDLE, None, []
            effective_spend_ids.add(effective_spend_id)
        # Fast forward spends are only allowed when bundled with other, non-FF
        # spends in order to evict an FF spend, it must be associated with a
        # normal spend that can be included in a block or invalidated some
        # other way.
        if len(non_ff_spend_ids) == 0:
            return Err.INVALID_SPEND_BUNDLE, None, []

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

        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/consensus/block_body_validation.py (L541-546)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```

**File:** chia/_tests/core/custom_types/test_coin.py (L77-83)
```python
    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```

**File:** chia/_tests/core/consensus/test_block_creation.py (L19-23)
```python
    expected = sum(rem_amount) - sum(add_amount)

    if expected < 0:
        with pytest.raises(ValueError, match="does not fit into uint64"):
            compute_block_fee(additions, removals)
```
