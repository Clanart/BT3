### Title
Mempool conflict eviction is not atomic with new-item admission, causing valid unconfirmed spends to be dropped on a single crafted spend bundle - ([File: chia/full_node/mempool_manager.py])

### Summary
`MempoolManager.add_spend_bundle()` removes the mempool items that conflict with an incoming (superseding) spend bundle *before* confirming that the new bundle can actually be admitted to the pool. If `Mempool.add_to_pool()` subsequently rejects the new item (e.g. `Err.INVALID_FEE_LOW_FEE`), the conflicting items that were already deleted are never restored, and the new item is not added either. The result is state that is "freed" (the old, previously-valid mempool items) while the code path that was supposed to replace them fails — an object-lifecycle handling bug in the same class as the reported PHP SoapServer issue (a persisted/cached object is invalidated/removed as part of an operation, but an error partway through the operation leaves callers with a dangling/inconsistent reference to state that no longer exists).

### Finding Description
In `add_spend_bundle()`:
```
chia/full_node/mempool_manager.py:648-655
if err is None:
    # No error, immediately add to mempool, after removing conflicting TXs.
    assert item is not None
    conflict = self.mempool.remove_from_pool(remove_items, MempoolRemoveReason.CONFLICT)
    info = self.mempool.add_to_pool(item)
    if info.error is not None:
        return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.FAILED, [], info.error)
    return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.SUCCESS, [*info.removals, conflict], None)
```
`self.mempool.remove_from_pool(remove_items, ...)` unconditionally deletes the previous conflicting items from the SQLite-backed `Mempool._items`/`tx` table (`chia/full_node/mempool.py:355-401`) before the new item's admission is confirmed. `add_to_pool()` (`chia/full_node/mempool.py:403-510`) can still fail and return `Err.INVALID_FEE_LOW_FEE` without ever touching `self._items`/the `tx` table:
```
chia/full_node/mempool.py:441-447
if cumulative_cost + item.cost <= self.mempool_info.max_block_clvm_cost:
    break
if priority > item.fee_per_virtual_cost:
    return MempoolAddInfo([], Err.INVALID_FEE_LOW_FEE)
```
This check runs only when the new item itself has a near-expiry `assert_before_height`/`assert_before_seconds` and a fee-per-virtual-cost too low to justify evicting other soon-expiring items. When this rejection occurs, the caller returns `FAILED`, but the earlier `remove_from_pool()` call has already permanently evicted the conflicting (previously valid, already-admitted) items. There is no rollback of that removal on the `add_to_pool()` failure path. The "old" mempool items are logically freed from the pool while the operation that was supposed to atomically replace them with the new item errors out, leaving the pool in a state inconsistent with what any caller (including the RPC caller who submitted the original still-valid transaction) expects.

### Impact Explanation
An unprivileged spend-bundle submitter who has a competing/superseding spend for a coin already occupying mempool space can trigger eviction of the original, currently-valid transaction without their own transaction taking its place, by crafting a bundle that: (1) is a superset of/conflicts with an already-admitted mempool item (making `remove_items` non-empty and `err is None` from `validate_spend_bundle`), and (2) itself has a near-expiry timelock condition and low fee-per-virtual-cost so `add_to_pool()` hits the `INVALID_FEE_LOW_FEE` branch. This silently discards the previously accepted, valid transaction from this node's mempool with no compensating admission — a spend-triggered mempool-processing regression that also produces mempool divergence between nodes that processed submissions in different orders (nodes that never saw the crafted bundle retain the original transaction; nodes that processed it lose it). Because mempool content ultimately determines block-candidate construction, this is a liveness/availability issue for honest, fee-paying transaction submitters, and a source of unnecessary retransmission/DoS pressure on the network, without requiring any privileged access, malicious peer/node, or protocol-authority position.

### Likelihood Explanation
The path is reachable purely through a normal `/push_tx` RPC call or peer transaction gossip — the same trust boundary as any wallet or `spend_bundle` submitter. Triggering it requires only crafting a bundle that (a) legitimately conflicts with (double-spends or supersedes) an existing mempool item, satisfying `validate_spend_bundle()`'s superset/conflict-removal logic, and (b) carries a `assert_before_height`/`assert_before_seconds` condition close to the current mempool cutoff with a low fee, which is fully attacker-controlled input (no signature/authorization on other coins is needed beyond spending one's own coin with the desired conditions). This makes the trigger straightforward and cheap to construct, though it depends on mempool occupancy/fee conditions at the time (near-full mempool with soon-expiring low-fee entries), which somewhat bounds exploitability compared to an always-on bug.

### Recommendation
Make the conflict-removal and new-item admission atomic: only call `remove_from_pool(remove_items, ...)` after confirming `add_to_pool(item)` would succeed, or re-insert the removed conflicting items if `add_to_pool()` returns an error. Concretely, restructure `add_spend_bundle()` to attempt admission first (or perform a dry-run capacity/fee check) and only commit the conflict removal once the new item's insertion is guaranteed to succeed, mirroring the existing rollback pattern already used elsewhere in `create_block_generator2()` for batch rejection (`committed_ff`/`committed_dedup` snapshot/restore, `chia/full_node/mempool.py:900-926`).

### Proof of Concept
1. Get a mempool close to `max_size_in_cost` with several low-fee items whose `assert_before_height`/`assert_before_seconds` are within the "soon-to-expire" cutoff window (`block_cutoff`/`time_cutoff` in `Mempool.add_to_pool()`).
2. Submit spend bundle `A` (any normal fee) that gets admitted, occupying coin `C`.
3. Submit spend bundle `B` that conflicts with/supersedes `A` on coin `C` (so `validate_spend_bundle()` returns `err is None` with `remove_items=[A.name]`), but craft `B` with its own `assert_before_height` close to `block_cutoff` and a fee-per-virtual-cost lower than at least one of the soon-to-expire items already in the pool (so `add_to_pool()` reaches the `priority > item.fee_per_virtual_cost` branch and returns `Err.INVALID_FEE_LOW_FEE`).
4. Observe: `add_spend_bundle()` returns `MempoolInclusionStatus.FAILED`, yet `A` has already been removed via `remove_from_pool()` at line 651 before `add_to_pool()` was even called — the coin `C`'s previously valid, fee-paying transaction is now gone from the mempool with nothing to replace it, and must be resubmitted. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** chia/full_node/mempool_manager.py (L648-655)
```python
        if err is None:
            # No error, immediately add to mempool, after removing conflicting TXs.
            assert item is not None
            conflict = self.mempool.remove_from_pool(remove_items, MempoolRemoveReason.CONFLICT)
            info = self.mempool.add_to_pool(item)
            if info.error is not None:
                return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.FAILED, [], info.error)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.SUCCESS, [*info.removals, conflict], None)
```

**File:** chia/full_node/mempool.py (L355-401)
```python
    def remove_from_pool(self, items: list[bytes32], reason: MempoolRemoveReason) -> MempoolRemoveInfo:
        """
        Removes an item from the mempool.
        """
        if items == []:
            return MempoolRemoveInfo({}, reason)

        removed_items: list[MempoolItemInfo] = []
        if reason != MempoolRemoveReason.BLOCK_INCLUSION:
            for batch in to_batches(items, SQLITE_MAX_VARIABLE_NUMBER):
                args = ",".join(["?"] * len(batch.entries))
                with self._db_conn:
                    cursor = self._db_conn.execute(
                        f"SELECT name, cost, fee FROM tx WHERE name in ({args})", batch.entries
                    )
                    for row in cursor:
                        name = bytes32(row[0])
                        internal_item = self._items[name]
                        item = MempoolItemInfo(int(row[1]), int(row[2]), internal_item.height_added_to_mempool)
                        removed_items.append(item)

        removed_internal_items = {name: self._items.pop(name) for name in items}

        for batch in to_batches(items, SQLITE_MAX_VARIABLE_NUMBER):
            args = ",".join(["?"] * len(batch.entries))
            with self._db_conn:
                cursor = self._db_conn.execute(
                    f"SELECT SUM(cost), SUM(fee) FROM tx WHERE name in ({args})", batch.entries
                )
                cost_to_remove, fee_to_remove = cursor.fetchone()

                self._db_conn.execute(f"DELETE FROM tx WHERE name in ({args})", batch.entries)
                self._db_conn.execute(f"DELETE FROM spends WHERE tx in ({args})", batch.entries)

            self._total_cost -= cost_to_remove
            self._total_fee -= fee_to_remove
            assert self._total_cost >= 0
            assert self._total_fee >= 0

        if reason != MempoolRemoveReason.BLOCK_INCLUSION:
            info = FeeMempoolInfo(
                self.mempool_info, self.total_mempool_cost(), self.total_mempool_fees(), datetime.now()
            )
            for iteminfo in removed_items:
                self.fee_estimator.remove_mempool_item(info, iteminfo)

        return MempoolRemoveInfo(removed_internal_items, reason)
```

**File:** chia/full_node/mempool.py (L403-450)
```python
    def add_to_pool(self, item: MempoolItem) -> MempoolAddInfo:
        """
        Adds an item to the mempool by kicking out transactions (if it doesn't fit), in order of increasing fee per cost
        """

        assert item.fee < MEMPOOL_ITEM_FEE_LIMIT
        assert item.conds is not None
        assert item.cost <= self.mempool_info.max_block_clvm_cost

        removals: list[MempoolRemoveInfo] = []

        # we have certain limits on transactions that will expire soon
        # (in the next 15 minutes)
        block_cutoff = self._block_height + 48
        time_cutoff = self._timestamp + 900
        if (item.assert_before_height is not None and item.assert_before_height < block_cutoff) or (
            item.assert_before_seconds is not None and item.assert_before_seconds < time_cutoff
        ):
            # this lists only transactions that expire soon, in order of
            # lowest fee rate along with the cumulative cost of such
            # transactions counting from highest to lowest fee rate
            cursor = self._db_conn.execute(
                """
                SELECT name,
                    priority,
                    SUM(cost) OVER (ORDER BY priority DESC, seq ASC) AS cumulative_cost
                FROM tx
                WHERE assert_before_seconds IS NOT NULL AND assert_before_seconds < ?
                    OR assert_before_height IS NOT NULL AND assert_before_height < ?
                ORDER BY cumulative_cost DESC
                """,
                (time_cutoff, block_cutoff),
            )
            to_remove: list[bytes32] = []
            for row in cursor:
                name, priority, cumulative_cost = row

                # there's space for us, stop pruning
                if cumulative_cost + item.cost <= self.mempool_info.max_block_clvm_cost:
                    break

                # we can't evict any more transactions, abort (and don't
                # evict what we put aside in "to_remove" list)
                if priority > item.fee_per_virtual_cost:
                    return MempoolAddInfo([], Err.INVALID_FEE_LOW_FEE)
                to_remove.append(name)

            removals.append(self.remove_from_pool(to_remove, MempoolRemoveReason.EXPIRED))
```
