## Title
HTTP/2-Rapid-Reset-style CPU exhaustion via unbounded cheap resubmission of expensive-to-validate spend bundles - (File: chia/full_node/full_node.py, chia/full_node/mempool_manager.py)

### Summary
`FullNode.add_transaction()` performs the expensive part of transaction admission — CLVM execution and BLS aggregate-signature verification via `MempoolManager.pre_validate_spendbundle()` — *before* the cheap fee/capacity/conflict checks in `MempoolManager.validate_spend_bundle()` are applied. When the cheap checks subsequently reject the bundle (e.g. `INVALID_FEE_TOO_CLOSE_TO_ZERO`, `INVALID_FEE_LOW_FEE`, `MEMPOOL_CONFLICT` handled elsewhere, capacity-based rejection, etc.), the final `else` branch calls `self.mempool_manager.remove_seen(spend_name)` [1](#0-0) . This clears the de-duplication marker for that exact transaction hash, so the identical spend bundle can be resubmitted immediately and will re-trigger the full, expensive `pre_validate_spendbundle()` pipeline again — with no accumulating cost to the submitter.

### Finding Description
The admission pipeline is:

1. `add_transaction()` checks `seen()`/`in_flight()` for a fast, cheap de-dup path [2](#0-1) .
2. It marks the bundle `in_flight` and calls `pre_validate_spendbundle()`, which runs CLVM execution (`validate_clvm_and_signature`, up to `max_tx_clvm_cost` = half of `MAX_BLOCK_COST_CLVM`) and BLS signature validation in the executor pool [3](#0-2) . This is precisely the "expensive work" analog to HTTP/2 stream/header processing in the advisory.
3. After that expensive step succeeds, `add_and_maybe_pop_seen()` marks the bundle `seen` [4](#0-3) .
4. Only *afterwards* does `MempoolManager.add_spend_bundle()` → `validate_spend_bundle()` perform the comparatively cheap fee/capacity/conflict checks (`cost > max_tx_clvm_cost`, `fee_per_cost` vs `nonzero_fee_minimum_fpc`, `min_fee_rate`, double-spend/conflict detection) [5](#0-4) .
5. If this final step returns FAILED (not `MEMPOOL_CONFLICT`, not pending), `add_transaction()`'s `else` branch removes the `seen` marker unconditionally [1](#0-0) .

Because `remove_seen()` clears the entry entirely (not rate-limited, not counted) [6](#0-5) , the same spend bundle hash is no longer "seen," so a subsequent identical submission again passes the cheap `seen()`/`in_flight()` gate at the top of `add_transaction()` and forces a full re-execution of the expensive CLVM/BLS validation step. This is the direct structural analog of the HTTP/2 Rapid Reset bug class: a client-controlled, near-zero-cost action (resubmitting a rejected/cancelled bundle) forces the server to redo maximum-cost work (CLVM execution up to `max_tx_clvm_cost`, aggregate signature verification) repeatedly, without the cost ever being "banked" against the attacker.

Two reachable amplification paths exist:
- **RPC path**: `/push_tx` calls `FullNode.add_transaction()` directly, bypassing the per-peer `TransactionQueue` deficit-round-robin backpressure described for P2P-sourced transactions [7](#0-6) . A local RPC caller (in-scope actor category) can therefore hammer `add_transaction()` directly with no per-source throttling other than the mempool's own admission logic.
- **P2P path**: even though `TransactionQueue` throttles concurrent submissions per peer by CLVM-cost deficit, that mechanism only limits *queueing*, not *repeated resubmission over time* of transactions that get rejected and then have their `seen` marker cleared — nothing prevents the same peer from resending the identical bundle indefinitely once it is no longer `seen`.

The per-item CLVM cost ceiling (`max_tx_clvm_cost`, half of `MAX_BLOCK_COST_CLVM`) and the mandatory BLS signature check make each single validation attempt potentially very expensive in wall-clock CPU/executor time; the `_worker_queue_size` counter and the validation-timeout mechanism (`validation_timeout`, "duration guard") exist to bound *individual* call latency, not the aggregate rate of expensive submissions, so a stream of always-just-below-timeout, always-rejected-for-fee-reasons bundles can continuously consume the shared executor pool (`MempoolManager.pool`) used for all pending transaction validation, delaying/starving legitimate transaction admission for other users — a spend-triggered transaction-processing slowdown/halt.

### Impact Explanation
An attacker who owns even a single small-value coin can construct one (or a handful of) maximally-costly-but-otherwise-valid spend bundle(s) that are guaranteed to be rejected on the cheap fee/capacity check (e.g., zero fee while the mempool is at or near capacity, or deliberately racing to lose a `MEMPOOL_CONFLICT`/fee-rate comparison). Because rejection via the `FAILED` path clears the `seen` marker, the attacker can resubmit the same bundle (or trivially many similar near-max-cost bundles built from the same coin set via minor fee variations, since fee changes the hash) continuously, forcing the node's shared validation executor to repeatedly perform near-maximum CLVM execution and BLS verification work. This degrades or halts transaction-processing throughput for all other mempool submitters/wallets connected to that node — the impact category explicitly allowed by scope ("a spend-triggered transaction-processing halt"). Because the RPC (`/push_tx`) path bypasses the peer-level deficit queue entirely, the attack is especially effective from a local/RPC-reachable caller.

### Likelihood Explanation
Medium. It does not require any privileged access, a malicious peer implementation, or protocol-layer tricks — only the ability to submit spend bundles for coins the attacker owns (or arbitrary already-spent/no-fee coins that still pass CLVM execution and reach the fee/capacity check) via the standard `add_transaction`/`push_tx`/`new_transaction` interfaces available to any wallet user or RPC caller. The main precondition is engineering a bundle that: (a) is CLVM-valid and executes to near `max_tx_clvm_cost` (so each retry is maximally expensive), and (b) reliably fails the cheap fee/capacity gate so `remove_seen()` triggers on every attempt. Both are attacker-controllable.

### Recommendation
- Do not unconditionally clear the `seen` marker on `FAILED` outcomes in `FullNode.add_transaction()`; instead, keep a short-lived negative cache entry (similar to the `ValidationError` handling that intentionally keeps bundles in `seen`) for bundles that failed pure fee/capacity checks, distinguishing "retry later because mempool state may change" from "immediately retryable for free."
- Introduce a per-source (per-peer / per-RPC-caller) rate limit or increasing cost/backoff for repeated resubmission of the same spend-bundle hash that has already consumed a full expensive validation cycle and failed non-transiently.
- Route RPC-submitted transactions (`/push_tx`) through the same (or an equivalent) backpressure/deficit mechanism used for peer-submitted transactions (`TransactionQueue`), rather than calling `add_transaction()` directly with unmetered concurrency.

### Proof of Concept
1. Attacker controls a coin and constructs a `SpendBundle` whose CLVM execution cost is close to `max_tx_clvm_cost` (half of `MAX_BLOCK_COST_CLVM`) but sets `fee = 0`.
2. Ensure the node's mempool is at (or drive it to) full capacity so the cheap capacity check in `validate_spend_bundle()` rejects the bundle with `INVALID_FEE_TOO_CLOSE_TO_ZERO` / `INVALID_FEE_LOW_FEE` [8](#0-7)  — this happens only *after* `pre_validate_spendbundle()` has already performed the full CLVM execution and signature check.
3. `add_transaction()`'s final `else` branch calls `remove_seen(spend_name)` [1](#0-0) .
4. Attacker immediately resubmits the exact same (or a trivially fee-bumped, still-losing) bundle via `push_tx` RPC (bypassing peer queue backpressure) or via `new_transaction`/`respond_transaction`. `seen()` now returns `False`, so the bundle passes the cheap gate again and forces another full `pre_validate_spendbundle()` cycle.
5. Repeating steps 3–4 in a tight loop drives the shared `MempoolManager.pool` executor to continuously perform near-maximum-cost CLVM/BLS validation work for a single attacker at negligible cost, delaying validation of legitimate transactions submitted concurrently by other users — the rapid-reset-style cost asymmetry.

### Citations

**File:** chia/full_node/full_node.py (L3041-3045)
```python
        if self.mempool_manager.get_spendbundle(spend_name) is not None:
            self.mempool_manager.remove_seen(spend_name)
            return MempoolInclusionStatus.SUCCESS, None
        if self.mempool_manager.seen(spend_name) or self.mempool_manager.in_flight(spend_name):
            return MempoolInclusionStatus.FAILED, Err.ALREADY_INCLUDING_TRANSACTION
```

**File:** chia/full_node/full_node.py (L3081-3081)
```python
        self.mempool_manager.add_and_maybe_pop_seen(spend_name)
```

**File:** chia/full_node/full_node.py (L3142-3144)
```python
        else:
            self.mempool_manager.remove_seen(spend_name)
            self.log.debug(f"Wasn't able to add transaction with id {spend_name}, status {status} error: {error}")
```

**File:** chia/full_node/mempool_manager.py (L513-515)
```python
    def remove_seen(self, bundle_hash: bytes32) -> None:
        if bundle_hash in self.seen_bundle_hashes:
            self.seen_bundle_hashes.pop(bundle_hash)
```

**File:** chia/full_node/mempool_manager.py (L548-559)
```python
        self._worker_queue_size += 1
        try:
            flags = get_flags_for_height_and_constants(self.peak.height, self.constants)
            sbc: SpendBundleConditions
            sbc, new_cache_entries, duration = await self.pool.run_in_loop(
                validate_clvm_and_signature,
                spend_bundle,
                self.max_tx_clvm_cost,
                self.constants,
                flags | MEMPOOL_MODE,
                nice=(5, -fee_per_cost),
            )
```

**File:** chia/full_node/mempool_manager.py (L816-838)
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

        fees_per_cost: float = fees / cost
        # If pool is at capacity check the fee, if not then accept even without the fee
        if self.mempool.at_full_capacity(cost):
            if fees_per_cost < self.nonzero_fee_minimum_fpc:
                return Err.INVALID_FEE_TOO_CLOSE_TO_ZERO, None, []
            min_fee_rate = self.mempool.get_min_fee_rate(cost)
            if min_fee_rate is None:
                return Err.INVALID_COST_RESULT, None, []
            if fees_per_cost <= min_fee_rate:
                return Err.INVALID_FEE_LOW_FEE, None, []
```

**File:** .cursor/context/full-node.md (L74-74)
```markdown
- `/push_tx` calls `FullNode.add_transaction()` directly and returns only `FAILED` as an `RpcError`; it does not use the peer transaction queue. Wallet P2P `send_transaction` enqueues a `TransactionQueueEntry` (trusted peers get high-priority treatment), waits up to 45 seconds on `queue_entry.done`, and returns `PENDING` on timeout.
```
