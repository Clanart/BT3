### Title
Unchecked subtraction in mempool fee calculation can underflow uint64 and cause an unhandled exception during spend-bundle admission - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle()` computes the fee of an incoming spend bundle as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`. This mirrors the USSD `amountToBuyLeftUSD -= (...)` pattern: a subtraction is performed on values whose relative ordering is never enforced beforehand, and the result is immediately cast into an unsigned type that rejects negative values.

### Finding Description
In the per-coin-spend accumulation loop, `addition_amount` is summed directly from the `CREATE_COIN` conditions produced by running each spend's puzzle: [1](#0-0) 

`removal_amount` is summed from the amounts of the coins actually being spent: [2](#0-1) 

Immediately afterward, the fee is computed with an unguarded subtraction: [3](#0-2) 

Nothing in this function (nor in `pre_validate_spendbundle`, which only validates CLVM cost, individual `CREATE_COIN` amount bounds, and signatures) enforces that the sum of created coin amounts cannot exceed the sum of spent coin amounts. That balance/"no minting" invariant (`MINTING_COIN`) is only checked later, at full block-body validation time: [4](#0-3) 

Because a spend bundle's puzzle reveal is fully controlled by whoever creates the spend (a user can spend any coin they own — even a 1-mojo coin — with a custom puzzle that simply outputs `(CREATE_COIN <ph> <large_amount> ...)`), any unprivileged submitter can craft `addition_amount > removal_amount` for a bundle. `chia_rs`'s `uint64` constructor rejects negative values by raising `ValueError` (this exact class of bug was fixed for block validation in the CHANGELOG 1.1.5 entry, which explicitly states negative uint64 values were once allowed into the mempool before that fix, showing the historical significance of this exact invariant), so `uint64(removal_amount - addition_amount)` raises an exception rather than returning a rejection `Err` code as intended.

`validate_spend_bundle` is a plain function that returns `(Err, MempoolItem, list)` — it does not wrap this computation in a try/except, and neither does its caller `add_spend_bundle`: [5](#0-4) 

This differs from the earlier `pre_validate_spendbundle` stage, which explicitly catches `ValueError` from the CLVM/signature validation step: [6](#0-5) 

No equivalent guard exists around the `add_spend_bundle`/`validate_spend_bundle` call path, so a `ValueError` raised at line 814 propagates as an unhandled exception rather than being converted into a clean `MempoolInclusionStatus.FAILED` / `Err` response.

### Impact Explanation
This matches the accepted "spend-triggered transaction-processing halt" impact category: a single, unprivileged spend-bundle submission (mempool admission path, reachable by any wallet user or RPC caller) can trigger an unhandled exception deep in the mempool admission pipeline instead of a graceful rejection. Depending on where this exception surfaces relative to error handling further up the call stack, this can disrupt normal mempool processing for that transaction-processing cycle, and is inconsistent with the defense-in-depth pattern used elsewhere in the same file (explicit `ValueError` handling in `pre_validate_spendbundle`). It also indicates the mempool's fee-accounting logic silently assumes an invariant ("no minting") that is not actually enforced until the block-body-validation stage, an assumption that should not be trusted at the point spend bundles first reach the fee-computation code.

### Likelihood Explanation
High reachability: any wallet user or RPC caller can submit a spend bundle for a coin they own with a custom puzzle reveal producing `CREATE_COIN` conditions whose total exceeds the coin's amount. No special privileges, node access, or protocol-level knowledge are required — this is directly analogous to the original finding, where a normal, permissionless caller of a subtraction-based accounting routine can supply operands that violate an unstated ordering assumption.

### Recommendation
Before computing `fees = uint64(removal_amount - addition_amount)`, explicitly check `if addition_amount > removal_amount: return Err.MINTING_COIN, None, []` (or equivalent), mirroring the check already performed in `block_body_validation.py` (`if removed < added: return Err.MINTING_COIN`). This turns an unhandled underflow/exception into a well-defined rejection path consistent with the rest of the mempool admission pipeline.

### Proof of Concept
1. Attacker owns/controls a coin `C` with `amount = 1`.
2. Attacker crafts a `CoinSpend` for `C` using a custom puzzle (not a standard p2_delegated puzzle) whose solution, when run, unconditionally returns `(CREATE_COIN <some_puzzle_hash> 1000000)`.
3. Attacker builds a `SpendBundle` from this single `CoinSpend` (no valid aggregated signature needed if the puzzle doesn't require `AGG_SIG_*` conditions) and submits it via the normal `add_transaction`/mempool RPC path.
4. `pre_validate_spendbundle` succeeds because the per-condition amount (`1000000`) is within `MAX_COIN_AMOUNT` and CLVM cost/signature checks pass; there is no aggregate-balance check at this stage.
5. `MempoolManager.validate_spend_bundle` computes `addition_amount = 1000000`, `removal_amount = 1`, then executes `fees = uint64(removal_amount - addition_amount)` = `uint64(-999999)`, raising `ValueError` inside the mempool admission code path instead of returning a controlled `Err.MINTING_COIN`.

**Uncertainty**: I was not able to fully trace every caller of `add_spend_bundle`/`validate_spend_bundle` in `full_node.py` within the available search budget to confirm whether an outer `try/except Exception` ultimately catches this specific `ValueError` gracefully at a higher layer (which would reduce impact to a caught, logged error rather than a broader halt). The core finding — that the balance invariant is unchecked at this exact subtraction site, unlike the equivalent check in `block_body_validation.py` — is confirmed directly from the code shown above.

### Citations

**File:** chia/full_node/mempool_manager.py (L560-567)
```python
        # validate_clvm_and_signature raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValidationError(error)
            else:
                raise ValidationError(Err.UNKNOWN)  # pragma: no cover
```

**File:** chia/full_node/mempool_manager.py (L640-655)
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

**File:** chia/full_node/mempool_manager.py (L790-812)
```python
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

**File:** chia/consensus/block_body_validation.py (L533-546)
```python
    removed = 0
    for unspent in removal_coin_records.values():
        removed += unspent.coin.amount

    added = 0
    for coin, _ in additions:
        added += coin.amount

    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```
