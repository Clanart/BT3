### Title
Unlocked gap between mempool insertion and its next dereference lets a concurrent block acceptance evict the item, crashing transaction processing - (File: chia/full_node/full_node.py)

### Summary
`FullNode.add_transaction()` inserts a spend bundle into the mempool while holding `blockchain.priority_mutex`, releases that lock, and only afterwards fetches the just-added `MempoolItem` and asserts it is non-`None`. Between the lock release and the fetch there is no reference or lock protecting the item, so a higher-priority concurrent task (block acceptance / `new_peak()`) can evict it in that gap, causing an `AssertionError`. This is structurally the same class of bug as CVE-2020-28327: an object is created/admitted, control returns without holding a lock or reference on it, and another thread/task can free it before the creating task's next dereference.

### Finding Description
In `chia/full_node/full_node.py`, `add_transaction()`: [1](#0-0) 

acquires `self.blockchain.priority_mutex` at `BlockchainMutexPriority.low`, calls `self.mempool_manager.add_spend_bundle(...)`, and captures `status`/`error` — all while the lock is held. The `async with` block then ends (lock released), and only *after* release does the code check `if status == MempoolInclusionStatus.SUCCESS` and fetch the item: [2](#0-1) 

```
        if status == MempoolInclusionStatus.SUCCESS:
            ...
            mempool_item = self.mempool_manager.get_mempool_item(spend_name)
            assert mempool_item is not None
```

`add_spend_bundle` itself does not return or pin a reference to the `MempoolItem`; it only returns a `MempoolInclusionStatus` and a name. Meanwhile, block acceptance (`MempoolManager.new_peak()`) also runs under the same `priority_mutex`, but at a *higher* priority than transaction processing, per the project's own concurrency notes: "Block additions use high-priority blockchain locking; transaction admission uses low-priority locking..." [3](#0-2) . `new_peak()` walks spent coins and evicts conflicting/now-invalid mempool items via `remove_from_pool()`, which pops the item straight out of `Mempool._items` and the backing SQLite table: [4](#0-3) 

Because the `priority_mutex` is a `PriorityMutex` that lets lower-priority holders be preempted between the transaction admission's lock release and its subsequent unlocked read of the same item, a race window exists: task A (this spend bundle's `add_transaction`) releases the lock after successfully inserting the item; task B (a newly accepted block, higher priority) acquires the lock and evicts that very item (e.g., because the new block spends a conflicting coin, or the item's timelock/height assertions are no longer valid at the new peak); task A resumes, calls `get_mempool_item(spend_name)`, gets `None`, and hits `assert mempool_item is not None`, raising `AssertionError`.

This mirrors the Asterisk CVE precisely: the "dialog" (mempool item) is admitted by thread A without being locked/referenced across the gap to its next use, and thread B (block-acceptance path) is legitimately allowed to free it in that gap.

### Impact Explanation
Reaching this path only requires submitting an ordinary spend bundle via RPC `push_tx` or peer `NewTransaction`/`RespondTransaction`, i.e., an unprivileged wallet user or offer counterparty action — no malicious peer or node compromise needed. The trigger condition (a block landing that conflicts with/invalidates the just-added item) is a normal, honest network event, meaning any submitter can be the "thread A" whose task crashes when timing lines up with block production. An unhandled `AssertionError` inside the coroutine servicing `add_transaction` aborts that transaction-processing call path, denying inclusion feedback/broadcast for that spend and constituting a spend-triggered transaction-processing halt for the affected request, matching the "impact" criteria (transaction-processing halt).

### Likelihood Explanation
The race window is narrow (one lock release + one dict/SQLite lookup with no `await` in between in the fast path), which limits — but does not eliminate — the probability of hitting it on any single submission. It requires: (1) a spend bundle admitted successfully, and (2) a concurrent, higher-priority block-acceptance task evicting that exact item before the `get_mempool_item` call resumes. Because full nodes receive new blocks roughly every ~9 seconds network-wide and process many simultaneous transaction submissions, the window is real but timing-dependent, making this a plausible-but-lower-frequency Medium-severity condition, consistent with the CVSS AC:H rating of the original CVE.

### Recommendation
Move the `status == MempoolInclusionStatus.SUCCESS` handling and the `get_mempool_item(spend_name)` call inside the same `async with self.blockchain.priority_mutex.acquire(...)` block that performs the insertion, or have `add_spend_bundle`/`MempoolAddInfo` directly return the resulting `MempoolItem` so no second, unlocked lookup is needed. At minimum, replace the `assert mempool_item is not None` with a defensive check that treats a vanished item as a benign "no-op" (log and return) rather than raising, so an intervening eviction cannot crash the request path.

### Proof of Concept
1. Submit spend bundle `SB1` via `push_tx`/`NewTransaction`; `add_transaction()` validates it and enters the `priority_mutex` critical section, calls `add_spend_bundle`, gets `status = SUCCESS`, then releases the lock (`chia/full_node/full_node.py:3092-3102`).
2. Immediately, before the task resumes, have a full block arrive and be accepted whose `new_peak()` processing (higher-priority lock holder) evicts `SB1`'s mempool item — e.g., because the block includes a conflicting spend of one of `SB1`'s coins, or `SB1`'s time-lock condition is no longer satisfied at the new peak (`chia/full_node/mempool_manager.py` `new_peak()` eviction path calling `Mempool.remove_from_pool`, `chia/full_node/mempool.py:355-376`).
3. Task resumes at `chia/full_node/full_node.py:3110-3111`, calls `get_mempool_item(spend_name)` → `None`, and `assert mempool_item is not None` raises `AssertionError`, aborting normal completion of `add_transaction` for `SB1`.

### Citations

**File:** chia/full_node/full_node.py (L3092-3102)
```python
        async with self.blockchain.priority_mutex.acquire(priority=BlockchainMutexPriority.low):
            if self.mempool_manager.get_spendbundle(spend_name) is not None:
                self.mempool_manager.remove_seen(spend_name)
                return MempoolInclusionStatus.SUCCESS, None
            if self.mempool_manager.peak is None:
                return MempoolInclusionStatus.FAILED, Err.MEMPOOL_NOT_INITIALIZED
            info = await self.mempool_manager.add_spend_bundle(
                transaction, cost_result, spend_name, self.mempool_manager.peak.height
            )
            status = info.status
            error = info.error
```

**File:** chia/full_node/full_node.py (L3103-3111)
```python
        if status == MempoolInclusionStatus.SUCCESS:
            self.log.debug(
                f"Added transaction to mempool: {spend_name} mempool size: "
                f"{self.mempool_manager.mempool.total_mempool_cost()} normalized "
                f"{self.mempool_manager.mempool.total_mempool_cost() / 5000000}"
            )

            mempool_item = self.mempool_manager.get_mempool_item(spend_name)
            assert mempool_item is not None
```

**File:** .cursor/context/full-node.md (L34-34)
```markdown
- Block additions use high-priority blockchain locking; transaction admission uses low-priority locking after expensive pre-validation. This prevents transaction work from starving block acceptance while still making mempool insertion atomic with the chain peak used for validation.
```

**File:** chia/full_node/mempool.py (L355-376)
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
```
