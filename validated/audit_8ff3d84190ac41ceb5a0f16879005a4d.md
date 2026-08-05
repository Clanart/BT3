## Finding confirmed [1](#0-0) 

### Title
Unbounded growth of `DuplicateShredHandler::consumed` HashMap via crafted `num_chunks` values decoupled from buffer pruning trigger - (File: `gossip/src/duplicate_shred_handler.rs`)

### Summary
`DuplicateShredHandler::handle_shred_data` inserts into the `consumed: HashMap<Slot, bool>` map (via `should_consume_slot` -> `should_consume_slot()` helper) **before** validating `num_chunks`, but only inserts into `buffer: HashMap<(Slot, Pubkey), BufferEntry>` **after** that validation passes. This lets a remote peer grow `consumed` without growing `buffer`, decoupling `consumed`'s size from the only pruning trigger (`buffer.len() >= BUFFER_CAPACITY * 2`).

### Finding Description
The call order in `handle_shred_data` is: [2](#0-1) 

1. `should_consume_slot(chunk.slot)` calls `should_consume_slot(slot, blockstore, consumed)`, which does `consumed.entry(slot).or_insert_with(...)` — this **always** creates a new `consumed` entry for any never-before-seen slot in range, regardless of the chunk's validity. [3](#0-2) 
2. Only *after* that, `num_chunks`/`chunk_index` are checked: `if usize::from(num_chunks) > MAX_NUM_CHUNKS || chunk_index >= num_chunks { return Err(...) }`. If this fails, the function returns **before** the line `self.buffer.entry((chunk.slot, chunk.from)).or_default()` is ever reached. [4](#0-3) 

The CRDS-layer `Sanitize` impl for `DuplicateShred` only rejects `chunk_index >= num_chunks`; it does **not** enforce `num_chunks <= MAX_NUM_CHUNKS (3)`: [5](#0-4) 

So an attacker can craft a `DuplicateShred` with, e.g., `num_chunks = 4` and `chunk_index = 0` (which satisfies `chunk_index < num_chunks` and thus passes `Sanitize`), for any slot `s` satisfying `s > last_root && s < last_root + epoch_slots` (the only slot-range gate in `should_consume_slot`): [6](#0-5) 

Each such message adds one entry to `consumed` but zero entries to `buffer`, because the code errors out at the `MAX_NUM_CHUNKS` check before reaching the buffer-insertion line. The only place `consumed` is ever pruned is inside `maybe_prune_buffer`, which is gated exclusively on `buffer.len()`: [7](#0-6) 

If the attacker never sends chunks that pass the `MAX_NUM_CHUNKS` check, `buffer` never grows and this prune path never fires, so `consumed` grows without bound as new distinct slots enter the valid window on each epoch boundary (as `last_root` advances, previously-inserted `consumed` entries for slots that are now `<= last_root` are never removed, since removal only happens as a side effect of the buffer-size-triggered prune).

### Impact Explanation
This is unbounded, attacker-controlled memory growth in a core gossip-processing path (`DuplicateShredListener` / `DuplicateShredHandler`), reachable from any unprivileged gossip peer sending crafted `DuplicateShred` CRDS values. Sustained abuse over time (across multiple epochs, since the valid slot window slides forward with `last_root`) can grow `consumed` indefinitely, which is a non-RPC remote resource-exhaustion vector against a validator process — this fits the in-scope impact category of "non-RPC remote exhaustion/crash."

### Likelihood Explanation
The attack requires only sending gossip `DuplicateShred` push/pull messages from a single unprivileged peer, with a small crafted payload per distinct slot (only the header needs to be valid; `chunk` payload content is irrelevant since the function returns before reconstruction is attempted). The main external factors gating throughput are general gossip push/pull rate limits and CRDS-table-level per-value constraints (e.g., `MAX_DUPLICATE_SHREDS`/dedup behavior at the CRDS layer), which I was not able to fully trace with the remaining tool budget — these may reduce the *rate* of growth but do not change the fact that `consumed` growth is structurally decoupled from `buffer`-size-based pruning.

### Recommendation
Move the `consumed.entry(slot).or_insert_with(...)` bookkeeping to occur only after (or gate its insertion on) successful chunk validation, or track `consumed` size independently and cap/prune it based on its own size rather than solely on `buffer.len()`. Alternatively, validate `num_chunks <= MAX_NUM_CHUNKS` inside `Sanitize` (or immediately in `should_consume_slot`) so that invalid-num_chunks proofs are rejected before any map insertion.

### Proof of Concept
For thousands of distinct slots `s_i` satisfying `last_root < s_i < last_root + epoch_slots`, construct a `DuplicateShred` with `num_chunks = 4`, `chunk_index = 0`, `slot = s_i`, and a small `chunk` payload, and push/pull it into gossip from a single node. Observe `duplicate_shred_handler.consumed.len()` grow by 1 per distinct `s_i` while `duplicate_shred_handler.buffer.len()` stays at 0, since `handle_shred_data` returns `Err(InvalidChunkIndex)` at line 115-120 before reaching the buffer insertion at line 121, confirming `maybe_prune_buffer`'s `buffer.len() >= BUFFER_CAPACITY*2` trigger (line 170) is never satisfied while `consumed` keeps growing.

Note: I was unable to fully verify within the remaining tool budget whether CRDS-table-level limits (e.g. `MAX_DUPLICATE_SHREDS` in `gossip/src/duplicate_shred.rs`, referenced also in `crds_gossip.rs`/`crds_value.rs`) impose an effective cap on the number of distinct `DuplicateShred` values a single peer can inject per epoch window; this would only affect attack rate/scale, not the underlying logic flaw.

### Citations

**File:** gossip/src/duplicate_shred_handler.rs (L108-158)
```rust
    fn handle_shred_data(&mut self, chunk: DuplicateShred) -> Result<(), Error> {
        if !self.should_consume_slot(chunk.slot) {
            return Ok(());
        }
        let slot = chunk.slot;
        let num_chunks = chunk.num_chunks();
        let chunk_index = chunk.chunk_index();
        if usize::from(num_chunks) > MAX_NUM_CHUNKS || chunk_index >= num_chunks {
            return Err(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            });
        }
        let entry = self.buffer.entry((chunk.slot, chunk.from)).or_default();
        *entry
            .get_mut(usize::from(chunk_index))
            .ok_or(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            })? = Some(chunk);
        // If all chunks are already received, reconstruct and store
        // the duplicate slot proof in blockstore
        if entry.iter().flatten().count() == usize::from(num_chunks) {
            let chunks = std::mem::take(entry).into_iter().flatten();
            let slot_leader = self
                .leader_schedule_cache
                .slot_leader_at(slot, /*bank:*/ None)
                .ok_or(Error::UnknownSlotLeader(slot))?;
            let (shred1, shred2) =
                duplicate_shred::into_shreds(&slot_leader.id, chunks, self.shred_version)?;
            if !self.blockstore.has_duplicate_shreds_in_slot(slot) {
                self.blockstore.store_duplicate_slot(
                    slot,
                    shred1.into_payload(),
                    shred2.into_payload(),
                )?;

                // Notify duplicate consensus state machine. Drop if channel is over 50% full
                // to avoid blocking replay.
                if self.duplicate_slots_sender.len() * 2
                    < self.duplicate_slots_sender.capacity().unwrap_or(usize::MAX)
                {
                    self.duplicate_slots_sender
                        .try_send(slot)
                        .map_err(|_| Error::DuplicateSlotSenderFailure)?;
                }
            }
            self.consumed.insert(slot, true);
        }
        Ok(())
    }
```

**File:** gossip/src/duplicate_shred_handler.rs (L160-164)
```rust
    fn should_consume_slot(&mut self, slot: Slot) -> bool {
        slot > self.last_root
            && slot < self.last_root.saturating_add(self.cached_slots_in_epoch)
            && should_consume_slot(slot, &self.blockstore, &mut self.consumed)
    }
```

**File:** gossip/src/duplicate_shred_handler.rs (L166-173)
```rust
    fn maybe_prune_buffer(&mut self) {
        // The buffer is allowed to grow to twice the intended capacity, at
        // which point the extraneous entries are removed in linear time,
        // resulting an amortized O(1) performance.
        if self.buffer.len() < BUFFER_CAPACITY.saturating_mul(2) {
            return;
        }
        self.consumed.retain(|&slot, _| slot > self.last_root);
```

**File:** gossip/src/duplicate_shred_handler.rs (L213-221)
```rust
fn should_consume_slot(
    slot: Slot,
    blockstore: &Blockstore,
    consumed: &mut HashMap<Slot, bool>,
) -> bool {
    !*consumed
        .entry(slot)
        .or_insert_with(|| blockstore.has_duplicate_shreds_in_slot(slot))
}
```

**File:** gossip/src/duplicate_shred.rs (L366-374)
```rust
impl Sanitize for DuplicateShred {
    fn sanitize(&self) -> Result<(), SanitizeError> {
        sanitize_wallclock(self.wallclock)?;
        if self.chunk_index >= self.num_chunks {
            return Err(SanitizeError::IndexOutOfBounds);
        }
        self.from.sanitize()
    }
}
```
