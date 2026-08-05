Audit Report

## Title
Missing consumed-slot marking on `UnknownSlotLeader` error path allows repeated re-verification of duplicate-shred proofs - (File: gossip/src/duplicate_shred_handler.rs)

## Summary
In `DuplicateShredHandler::handle_shred_data`, once all chunks of a duplicate-shred proof are received, the buffer entry is drained with `std::mem::take(entry)` before the slot leader is resolved via `leader_schedule_cache.slot_leader_at(slot, None)`. [1](#0-0)  If that lookup returns `None`, the function returns `Err(Error::UnknownSlotLeader(slot))` via the `?` operator and never reaches `self.consumed.insert(slot, true)` on line 155. [2](#0-1) [3](#0-2) 

## Finding Description
`should_consume_slot` gates whether new chunks for a slot are buffered, checking the `consumed` map and, only on first access, falling back to `blockstore.has_duplicate_shreds_in_slot(slot)`. [4](#0-3)  Because the failed proof never reaches `store_duplicate_slot` (the error occurs before that call), `has_duplicate_shreds_in_slot(slot)` stays `false`, so `consumed` for that slot is never durably set to `true`. As a result, an attacker who can trigger an `UnknownSlotLeader` for a given slot can resend the same 3-chunk proof repeatedly; each time, `handle_shred_data` re-buffers the chunks, drains them, and re-runs `duplicate_shred::into_shreds`/`check_shreds` (deserialization plus Ed25519 signature verification) before failing again on the same lookup. [5](#0-4) 

## Impact Explanation
This matches the "non-RPC remote exhaustion" category: an unprivileged gossip peer can force repeated, CPU-costly cryptographic verification work on a validator without the target slot ever converging to a terminal "consumed" state, since the normal completion path (`consumed.insert(slot, true)` at line 155) is only reached inside the success branch and is skipped specifically on this error return.

## Likelihood Explanation
Exploitability depends on reliably producing a slot within the accepted window (`last_root < slot < last_root + cached_slots_in_epoch`, per `should_consume_slot`) for which `LeaderScheduleCache::slot_leader_at(slot, None)` returns `None`. [6](#0-5)  Inspection of `slot_leader_at`/`slot_leader_at_no_compute` shows the leader schedule cache is normally populated ahead of the root for the epoch(s) covering this window, via `set_root` computing the schedule for the new max leader-schedule epoch and via `cached_schedules` retaining recently computed epochs. [7](#0-6) [8](#0-7)  This means that under normal steady-state operation the schedule for slots inside `should_consume_slot`'s window should already be cached, making the `None` branch difficult to trigger deterministically by an external, unprivileged attacker without a validator-side pre-condition (e.g., cache eviction, a race during epoch rollover, or a stale/misconfigured leader schedule cache) that could not be fully confirmed from the code inspected. The underlying code defect (skipped `consumed.insert` on this error path) is nonetheless real and independent of this uncertainty.

## Recommendation
Ensure the slot is marked as consumed (or tracked via a separate "permanently failed, do not reprocess" state) even when `slot_leader_at` returns `None` after chunks have already been drained from the buffer — e.g., move the `consumed.insert(slot, true)` before the `slot_leader_at` lookup, or wrap the reconstruction logic so any terminal, non-retryable error still updates `consumed` for that slot.

## Proof of Concept
1. Identify or construct a slot within the `should_consume_slot` window (`last_root < slot < last_root + cached_slots_in_epoch`) for which `leader_schedule_cache.slot_leader_at(slot, None)` returns `None`.
2. Send 3 valid-format chunks (via `duplicate_shred::from_shred`) forming a complete duplicate-shred proof for that slot.
3. Observe `handle_shred_data` reassemble the chunks, call `slot_leader_at`, receive `None`, and return `Err(UnknownSlotLeader)` without reaching `self.consumed.insert(slot, true)`.
4. Confirm `blockstore.has_duplicate_shreds_in_slot(slot)` remains `false` and `should_consume_slot` continues returning `true` for the slot.
5. Resend the same 3-chunk proof; repeat step 3 indefinitely, each iteration re-incurring `into_shreds`/`check_shreds` deserialization and signature-verification cost.

### Citations

**File:** gossip/src/duplicate_shred_handler.rs (L108-137)
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
```

**File:** gossip/src/duplicate_shred_handler.rs (L155-155)
```rust
            self.consumed.insert(slot, true);
```

**File:** gossip/src/duplicate_shred_handler.rs (L160-164)
```rust
    fn should_consume_slot(&mut self, slot: Slot) -> bool {
        slot > self.last_root
            && slot < self.last_root.saturating_add(self.cached_slots_in_epoch)
            && should_consume_slot(slot, &self.blockstore, &mut self.consumed)
    }
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

**File:** ledger/src/leader_schedule_cache.rs (L71-94)
```rust
    pub fn set_root(&self, root_bank: &Bank) {
        let new_max_epoch = self
            .epoch_schedule
            .get_leader_schedule_epoch(root_bank.slot());
        let old_max_epoch = self.max_epoch.load(Ordering::Acquire);
        assert!(new_max_epoch >= old_max_epoch);

        if new_max_epoch > old_max_epoch {
            // Install the rooted schedule before publishing the epoch to readers.
            self.compute_leader_schedule(new_max_epoch, root_bank);
            let old_max_epoch = self.max_epoch.swap(new_max_epoch, Ordering::AcqRel);
            assert!(new_max_epoch >= old_max_epoch);
        }
    }

    pub fn slot_leader_at(&self, slot: Slot, bank: Option<&Bank>) -> Option<SlotLeader> {
        if let Some(bank) = bank {
            self.slot_leader_at_else_compute(slot, bank)
        } else if self.epoch_schedule.slots_per_epoch == 0 {
            None
        } else {
            self.slot_leader_at_no_compute(slot)
        }
    }
```

**File:** ledger/src/leader_schedule_cache.rs (L157-168)
```rust
    fn slot_leader_at_no_compute(&self, slot: Slot) -> Option<SlotLeader> {
        let (epoch, slot_index) = self.epoch_schedule.get_epoch_and_slot_index(slot);
        if let Some(ref fixed_schedule) = self.fixed_schedule {
            return Some(fixed_schedule.leader_schedule[slot_index]);
        }
        self.cached_schedules
            .read()
            .unwrap()
            .0
            .get(&epoch)
            .map(|schedule| schedule[slot_index])
    }
```
