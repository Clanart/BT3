### Title
Unhandled `OverflowError` in `MempoolManager.validate_spend_bundle()` from attacker-controlled negative fee (`uint64` underflow) — ([File: chia/full_node/mempool_manager.py])

### Summary
The Cosmos SDK advisory describes an integer overflow triggered by an attacker-controlled value (a malicious "deposit") flowing into an unguarded arithmetic/type-cast operation in reward accounting, causing a chain halt. The closest reachable analog in this codebase is the fee computation in `MempoolManager.validate_spend_bundle()`, where an attacker who fully controls the conditions of their own coin spend can make `addition_amount` exceed `removal_amount`, causing `uint64(removal_amount - addition_amount)` to raise an unhandled `OverflowError` rather than being rejected as an ordinary validation error.

### Finding Description
In `validate_spend_bundle()`, the mempool computes the total output value across all coin spends in the submitted bundle and subtracts it from the total input value to derive the fee: [1](#0-0) [2](#0-1) 

```
fees = uint64(removal_amount - addition_amount)
```

`removal_amount` and `addition_amount` are computed by summing `Coin.amount` values from `spend_conds.create_coin` (attacker-supplied CLVM output conditions) and from looked-up input coin records, respectively. Nothing in this function enforces `addition_amount <= removal_amount` before the subtraction; that invariant ("no minting") is normally enforced later, at full block-body validation time (`Err.MINTING_COIN` in `chia/consensus/block_body_validation.py`), not at the per-spend-bundle CLVM/condition level. A CLVM puzzle (fully attacker-controlled for a coin the attacker owns) can emit `CREATE_COIN` conditions whose individual amounts each satisfy the per-output `MAX_COIN_AMOUNT` check (enforced natively during condition parsing, as shown by `test_too_big_addition_amount`), but whose **sum** across multiple outputs exceeds the amount of the coin(s) actually being spent.

When `addition_amount > removal_amount`, `removal_amount - addition_amount` is negative, and casting a negative Python `int` to `uint64` raises `OverflowError` (confirmed by `Coin` construction behavior: `"can't convert negative int to unsigned"`): [3](#0-2) 

This exception is not caught anywhere inside `validate_spend_bundle()` or its caller `add_spend_bundle()`: [4](#0-3) 

`add_spend_bundle()` is invoked from `full_node.py` while processing incoming transactions/spend bundles (from wallets, RPC callers, and peers relaying `new_transaction`/`request_transaction`), and per its own docstring is expected to run "while the mempool/blockchain lock is held": [5](#0-4) 

An unhandled exception raised while this lock is held risks leaving the lock in an inconsistent state and aborting whatever task/coroutine (transaction-processing pipeline) was executing it, rather than producing the intended graceful `Err.INVALID_BLOCK_FEE_AMOUNT`/`Err.MINTING_COIN`-style rejection.

### Impact Explanation
This maps to the "spend-triggered transaction-processing halt" category explicitly listed as acceptable impact. A single unprivileged spend-bundle submitter — anyone able to reach mempool admission (wallet user, RPC caller, offer counterparty relaying a spend, or a peer relaying a transaction) — can trigger an unhandled Python exception deep inside mempool admission logic that runs under the blockchain/mempool lock, rather than being rejected through the intended, well-tested error path (`Err.INVALID_BLOCK_FEE_AMOUNT`, `Err.MINTING_COIN`). This is directly analogous to the Cosmos SDK bug class: an attacker-influenced value flows unchecked into an integer/type conversion that the code assumes will always be non-negative, and the resulting overflow/exception disrupts core transaction processing rather than being cleanly rejected.

### Likelihood Explanation
High reachability: the attacker only needs to construct a spend bundle spending a coin they own with a custom puzzle that returns multiple `CREATE_COIN` conditions whose amounts each pass the native `MAX_COIN_AMOUNT` per-output check but whose sum exceeds the coin's actual value — no special privileges, no cooperation from other nodes, and no reliance on existing puzzle bugs (the attacker writes/owns the puzzle themselves for the coin they are spending). This is a straightforward "self-minting" transaction that is expected to be rejected, but the specific code path taken (mempool admission, not block-body validation) computes the fee via an unguarded `uint64()` cast before any minting-specific rejection logic runs.

### Recommendation
In `validate_spend_bundle()`, explicitly check `addition_amount > removal_amount` before computing `fees`, and return a clean validation error (e.g., `Err.MINTING_COIN` or a dedicated mempool-level error) instead of relying on the `uint64()` constructor to raise on negative input. Additionally, wrap the fee computation (or the broader `validate_spend_bundle`/`add_spend_bundle` call sites) in defensive exception handling so that any unexpected arithmetic/type exception degrades to a bundle rejection rather than propagating out of code that holds the blockchain lock.

### Proof of Concept
1. Attacker creates/owns a coin `C` with `amount = 1` mojo, whose puzzle is fully attacker-controlled (e.g., a puzzle that ignores solution input and unconditionally returns hardcoded conditions).
2. Attacker crafts the puzzle/solution to emit two `CREATE_COIN` conditions, each with `amount = MAX_COIN_AMOUNT` (`0xffffffffffffffff`), to two puzzle hashes the attacker controls. Each individual amount is within the native per-output `MAX_COIN_AMOUNT` bound enforced during condition parsing (per `test_too_big_addition_amount`), so no per-output rejection occurs.
3. Attacker submits this `SpendBundle` via RPC/peer transaction relay.
4. During mempool admission, `addition_amount` (≈ `2 * MAX_COIN_AMOUNT`) exceeds `removal_amount` (`1`), so:
   ```
   fees = uint64(removal_amount - addition_amount)  # negative -> OverflowError
   ```
   raises an unhandled `OverflowError` inside `validate_spend_bundle()`, called from `add_spend_bundle()` while the mempool/blockchain lock is held, instead of returning the intended `Err.INVALID_BLOCK_FEE_AMOUNT`/minting-rejection path.

Note: I was unable to fully trace, within available search iterations, every call site in `chia/full_node/full_node.py` that invokes `add_spend_bundle`/`validate_spend_bundle` to confirm whether an outer `try/except Exception` wrapper (e.g., an API-request decorator) ultimately swallows this specific `OverflowError` before it can destabilize the lock or crash the node process. This should be verified directly against `chia/full_node/full_node.py`'s transaction-handling entry points (`add_transaction`, `respond_transaction`, etc.) to confirm the exact blast radius (isolated request failure vs. broader disruption).

### Citations

**File:** chia/full_node/mempool_manager.py (L609-616)
```python
        """
        Validates and adds to mempool a new_spend with the given
        SpendBundleConditions, and spend_name, and the current mempool. The mempool
        should be locked during this call (blockchain lock). If there are mempool
        conflicts, the conflicting spends might be removed (if the new spend is
        a superset of the previous). Otherwise, the new spend might be
        added to the potential pool.

```

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

**File:** chia/full_node/mempool_manager.py (L785-814)
```python
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

**File:** chia/_tests/core/custom_types/test_coin.py (L77-83)
```python
    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```
