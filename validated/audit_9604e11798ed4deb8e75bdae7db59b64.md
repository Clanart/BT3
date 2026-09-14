### Title
Mempool seen-cache poisoning on `peak is None` race leaves valid spend bundles permanently rejected - (File: `chia/full_node/full_node.py`)

### Summary
`FullNode.add_transaction()` marks every incoming spend bundle as "seen" in `MempoolManager.seen_bundle_hashes` before mempool admission is attempted, and is responsible for un-marking it via `remove_seen()` on every failure path so the sender can retry. One failure path — the `self.mempool_manager.peak is None` check taken inside the `blockchain.priority_mutex` — returns `FAILED, Err.MEMPOOL_NOT_INITIALIZED` without calling `remove_seen()`, unlike every sibling branch in the same function. This leaves the spend bundle's hash stuck in the seen cache, so all subsequent resubmissions of that exact bundle are short-circuited with `Err.ALREADY_INCLUDING_TRANSACTION` and never re-validated, until the FIFO-bounded cache evicts the entry.

### Finding Description
`add_transaction()` implements a "seen" cache to avoid re-validating spend bundles it has already processed: [1](#0-0) 

The cache is populated unconditionally once `pre_validate_spendbundle` succeeds: [2](#0-1) 

After that, the function re-checks state under the blockchain mutex and dispatches to `add_spend_bundle`: [3](#0-2) 

Every other exit from `add_transaction` that does **not** result in `MempoolInclusionStatus.SUCCESS` calls `self.mempool_manager.remove_seen(spend_name)` to release the entry so a legitimate retry can be revalidated — see the `get_spendbundle` dedupe branch at line 3094 and the generic failure branch: [4](#0-3) 

However, the `self.mempool_manager.peak is None` branch at line 3096-3097 is the single exception: it returns `FAILED, Err.MEMPOOL_NOT_INITIALIZED` and skips `remove_seen()` entirely: [5](#0-4) 

`MempoolManager.peak` legitimately transitions to `None`/gets reset during normal chain-reorg / mempool-rebuild activity (e.g. inside `new_peak()`), which is a routine, attacker-uncontrolled but attacker-*triggerable-by-timing* race: any unprivileged submitter whose transaction lands in this narrow window between the peak check earlier in the function (line 3049-3050) and the mutex-protected re-check (line 3096) will have their spend bundle marked "seen" but never actually validated against the mempool, and never un-marked.

Because `seen_bundle_hashes` is a simple insertion-ordered dict with only size-based (not time-based) eviction: [6](#0-5) 

the poisoned entry persists — blocking every future resubmission of that exact spend bundle with `ALREADY_INCLUDING_TRANSACTION` — until roughly 10,000 other distinct spend bundles are seen by the node, which could be a very long time on a quiet mempool.

This mirrors the Astro bug class precisely: an error-handling path that fails to account for the actual (transient) nature of the failure leaves stale cache/state behind, causing legitimate subsequent requests for the exact same resource/bundle to be denied until the cache entry is evicted.

### Impact Explanation
This is a spend-triggered transaction-processing halt for the affected coin spend: a wallet user (or any RPC caller / peer) whose transaction happens to race a mempool peak reset will have that specific, otherwise fully valid spend bundle silently and durably rejected by that full node with no way to force revalidation short of altering the spend bundle bytes (changing its hash) or waiting for cache churn. On a node with light mempool traffic this could persist indefinitely, effectively denying inclusion of that transaction via that node.

### Likelihood Explanation
Reaching this requires only a normal, unprivileged transaction submission (via RPC `push_tx` or wallet broadcast) that happens to overlap with the node's `mempool_manager.peak` being reset — which occurs during ordinary chain-tip transitions/reorgs handled by `new_peak()`, not an attacker-controlled or malicious-peer scenario. No special privileges, and no protocol violation, are needed; it's a timing race intrinsic to `add_transaction`'s two-phase peak check.

### Recommendation
Call `self.mempool_manager.remove_seen(spend_name)` before returning on the `self.mempool_manager.peak is None` branch (line 3096-3097) in `chia/full_node/full_node.py`, matching every other non-`SUCCESS` return path in `add_transaction`, so a transiently-rejected spend bundle can be revalidated on resubmission.

### Proof of Concept
1. Submit a valid `SpendBundle` via `add_transaction` (e.g., through the wallet's `SendTransaction` RPC handler or `full_node_api.send_transaction`) at the moment the full node is processing a new peak that clears/resets `mempool_manager.peak` (this happens as part of `MempoolManager.new_peak()`'s peak reassignment window, which can be induced in a test by concurrently farming a block while the transaction is in flight).
2. Observe that the call reaches the `self.mempool_manager.peak is None` branch, returning `(FAILED, Err.MEMPOOL_NOT_INITIALIZED)` without `remove_seen` being invoked (confirmable by asserting `full_node.mempool_manager.seen(spend_name)` is still `True` afterward, analogous to the existing test pattern in `chia/_tests/core/full_node/test_full_node.py:1562-1583` which validates the sibling "no-peak" early-return path but does not cover this later, mutex-protected peak-None branch).
3. Resubmit the identical `SpendBundle` (same `spend_name`) after the peak is restored; `add_transaction` returns `(FAILED, Err.ALREADY_INCLUDING_TRANSACTION)` at line 3044-3045 instead of validating it, and this persists on every retry until the 10,000-entry `seen_bundle_hashes` cache evicts the entry.

### Citations

**File:** chia/full_node/full_node.py (L3041-3046)
```python
        if self.mempool_manager.get_spendbundle(spend_name) is not None:
            self.mempool_manager.remove_seen(spend_name)
            return MempoolInclusionStatus.SUCCESS, None
        if self.mempool_manager.seen(spend_name) or self.mempool_manager.in_flight(spend_name):
            return MempoolInclusionStatus.FAILED, Err.ALREADY_INCLUDING_TRANSACTION
        self.log.debug(f"Processing transaction: {spend_name}")
```

**File:** chia/full_node/full_node.py (L3081-3081)
```python
        self.mempool_manager.add_and_maybe_pop_seen(spend_name)
```

**File:** chia/full_node/full_node.py (L3092-3100)
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
```

**File:** chia/full_node/full_node.py (L3142-3145)
```python
        else:
            self.mempool_manager.remove_seen(spend_name)
            self.log.debug(f"Wasn't able to add transaction with id {spend_name}, status {status} error: {error}")
        return status, error
```

**File:** chia/full_node/mempool_manager.py (L494-502)
```python
    def add_and_maybe_pop_seen(self, spend_name: bytes32) -> None:
        self.seen_bundle_hashes[spend_name] = spend_name
        while len(self.seen_bundle_hashes) > self.seen_cache_size:
            first_in = next(iter(self.seen_bundle_hashes.keys()))
            self.seen_bundle_hashes.pop(first_in)

    def seen(self, bundle_hash: bytes32) -> bool:
        """Return true if we saw this spendbundle recently"""
        return bundle_hash in self.seen_bundle_hashes
```
