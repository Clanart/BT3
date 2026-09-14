### Title
Usage of Uninitialized `maybe_gen` Variable in `create_block_generator` RPC Causes Unhandled Exception - (File: chia/full_node/full_node_rpc_api.py)

### Summary
The `FullNodeRpcApi.create_block_generator` endpoint assigns `maybe_gen` only inside a `try` block, but reads `maybe_gen` in a check that lives outside that `try`/`except`. If `MempoolManager.create_block_generator2()` raises before returning (rather than returning `None` or a value), `maybe_gen` is never bound in that scope, and the subsequent `if maybe_gen is not None:` check raises `UnboundLocalError`, an unhandled exception analogous to the "usage of uninitialized object" root cause in CVE-2023-24826.

### Finding Description
In `create_block_generator`, `maybe_gen` is declared/assigned only inside the `try:` block: [1](#0-0) 

```python
try:
    maybe_gen = self.service.mempool_manager.create_block_generator2(curr_l_tb.header_hash, 2.0)
    if maybe_gen is None:
        self.service.log.error(f"failed to create block generator, peak: {peak}")
    else:
        gen = maybe_gen
except Exception:
    self.service.log.exception(f"Error creating block generator, peak: {peak}")
self.service.log.info(f"Simulated block constructed in {time.monotonic() - start_time:0.2f} seconds")

if maybe_gen is not None:
```

The `except Exception:` clause swallows any exception raised by `create_block_generator2` (e.g. `Mempool.create_block_generator2`, which builds a block from mempool items using fast-forward/dedup logic and a Rust `BlockBuilder`) [2](#0-1)  but does not set `maybe_gen` to any value. Execution then falls through to line 1003, where `maybe_gen` is referenced — but since the assignment on line 994 never completed, Python raises `UnboundLocalError: local variable 'maybe_gen' referenced before assignment`. This mirrors the CVE-2023-24826 pattern: an exceptional/attacker-influenced code path skips initialization of a variable that is unconditionally read afterward, causing the request-handling code to crash instead of degrading gracefully.

This call executes while holding `self.service.blockchain.priority_mutex` at low priority [3](#0-2) , and the exception propagates out of the `async with` block (the lock itself is correctly released by the context manager, but the endpoint coroutine itself terminates via an unhandled exception rather than returning a JSON RPC error).

### Impact Explanation
Because `create_block_generator2` walks the entire mempool state (fast-forward singleton chaining, dedup bookkeeping, block-builder cost accounting) inside a loop where most per-item exceptions are already caught internally, only exceptions occurring outside that inner per-item `try/except` (e.g., in `AugSchemeMPL.aggregate`, DB cursor iteration, or `BlockBuilder` internals) would propagate out of `create_block_generator2` to trigger this bug. If reached, the RPC call fails with an unhandled server-side exception instead of a controlled error response, which is a denial-of-service style defect on that RPC endpoint reachable by a local RPC caller. It does not by itself enable unauthorized coin movement, supply inflation, or consensus divergence.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires that `create_block_generator2` throw an exception from a code region not already wrapped by its own inner `try/except Exception` (which already catches per-item `SkipDedup`/generic exceptions during the main scan loop). Triggering this reliably would need a specific mempool state that causes a failure in code outside that inner loop (e.g., signature aggregation or builder finalization). This is plausible but not trivially reproducible without further investigation into `BlockBuilder`/`AugSchemeMPL.aggregate` failure modes, which the available index does not fully expose.

### Recommendation
Initialize `maybe_gen` (e.g., `maybe_gen: NewBlockGenerator | None = None`) before the `try` block so the post-`except` check at line 1003 always references a bound variable regardless of whether an exception was raised, matching the fix pattern used for CVE-2023-24826 (ensure the referenced object/timer is always initialized before use, even on early-exit or exceptional paths).

### Proof of Concept
1. Start a full node with the RPC server enabled and call `create_block_generator` (an authorized local RPC caller can invoke it directly, or via `full_node_rpc_client.py`, which exposes this call) [4](#0-3) .
2. Populate the mempool with mempool items crafted (via normal `push_tx`/wallet spend submission) such that `Mempool.create_block_generator2` raises an exception outside its internal per-item `try/except` block — e.g., during signature aggregation or `BlockBuilder` finalization at the end of the scan loop [5](#0-4) .
3. Invoke the `create_block_generator` RPC while that mempool state is active.
4. Observe that the caught exception at line 999 logs the error but leaves `maybe_gen` unbound, and the subsequent `if maybe_gen is not None:` at line 1003 raises `UnboundLocalError`, causing the RPC handler coroutine to terminate with an unhandled exception rather than a structured RPC error response.

Note: Full confirmation of a concrete exception-triggering mempool state inside `create_block_generator2`'s non-guarded regions (signature aggregation / `BlockBuilder`) could not be completed within the available index; this would require deeper inspection of `chia_rs`'s `BlockBuilder` and `AugSchemeMPL.aggregate` failure conditions, which are outside the indexed Python source.

### Citations

**File:** chia/full_node/full_node_rpc_api.py (L972-973)
```python
        async with self.service.blockchain.priority_mutex.acquire(priority=BlockchainMutexPriority.low):
            peak: BlockRecord | None = self.service.blockchain.get_peak()
```

**File:** chia/full_node/full_node_rpc_api.py (L993-1003)
```python
            try:
                maybe_gen = self.service.mempool_manager.create_block_generator2(curr_l_tb.header_hash, 2.0)
                if maybe_gen is None:
                    self.service.log.error(f"failed to create block generator, peak: {peak}")
                else:
                    gen = maybe_gen
            except Exception:
                self.service.log.exception(f"Error creating block generator, peak: {peak}")
            self.service.log.info(f"Simulated block constructed in {time.monotonic() - start_time:0.2f} seconds")

            if maybe_gen is not None:
```

**File:** chia/full_node/mempool.py (L751-810)
```python
    def create_block_generator2(
        self, constants: ConsensusConstants, prev_tx_height: uint32, timeout: float
    ) -> NewBlockGenerator | None:
        fee_sum = 0  # Checks that total fees don't exceed 64 bits
        additions: list[Coin] = []
        removals: list[Coin] = []

        dedup_coin_spends = IdenticalSpendDedup()
        singleton_ff = SingletonFastForward()
        # Fast forward and dedup state committed so far from accepted batches,
        # used to rollback on batch rejection.
        committed_ff = singleton_ff.copy()
        committed_dedup = dedup_coin_spends.copy()
        log.info(f"Starting to make block, max cost: {self.mempool_info.max_block_clvm_cost}")
        generator_creation_start = monotonic()
        cursor = self._db_conn.execute("SELECT name, fee FROM tx ORDER BY priority DESC, seq ASC")
        builder = BlockBuilder()
        skipped_items = 0
        # the total (estimated) cost of the transactions added so far
        block_cost = 0
        added_spends = 0
        # the number of atoms and pairs accrued from committed batches so far.
        # We track these independently to stop before exceeding the block atom
        # and pair limits.
        added_atoms = 0
        added_pairs = 0
        # Track coins already committed to spend so we never add a conflicting
        # spend to the same block. `spent_coin_ids` holds coins from accepted
        # batches, `batch_spent_coin_ids` holds coins from the batch currently
        # being assembled (dropped if that batch is rejected).
        spent_coin_ids: set[bytes32] = set()
        batch_spent_coin_ids: set[bytes32] = set()

        batch_transactions: list[SpendBundle] = []
        batch_additions: list[Coin] = []
        batch_spends = 0
        batch_atoms = 0
        batch_pairs = 0
        # this cost only includes conditions and execution cost, not byte-cost
        batch_cost = 0

        for row in cursor:
            current_time = monotonic()
            if current_time - generator_creation_start >= timeout:
                log.info(f"exiting early, already spent {current_time - generator_creation_start:0.2f} s")
                break

            # Stop scanning once too many items don't fit, rather than burning
            # the timeout on fast-forward and dedup work. Unlike block cost, the
            # atom and pair budgets aren't freed by compression, so once
            # saturated every further item keeps getting skipped here.
            if skipped_items >= MAX_SKIPPED_ITEMS:
                log.info("Skipped %d mempool items, stopping block creation", skipped_items)
                break

            name = bytes32(row[0])
            fee = int(row[1])
            item = self._items[name]
            try:
                assert item.conds is not None
```

**File:** chia/full_node/full_node_rpc_client.py (L1-1)
```python
from __future__ import annotations
```
