This confirms the analysis. Routine automatic ledger cleanup, `BlockstoreCleanupService::cleanup_ledger`, uses `PurgeType::CompactionFilter`, not `PurgeType::Exact`, as shown at [1](#0-0) . The `PurgeType::CompactionFilter` variant explicitly skips `purge_special_columns_exact` entirely, deferring transaction-status/address-signature cleanup to RocksDB's compaction filter mechanism instead: [2](#0-1) .

The expensive `purge_special_columns_exact` loop at issue is only reached via `PurgeType::Exact`, which is used by `do_purge_slot_cleanup_chaining` for single-slot purges (duplicate/dead slot handling) at [3](#0-2) , not by the routine background cleanup that runs on every rooted slot interval. That path operates one slot at a time (`slot..=slot`), so it inherently scales with transaction volume within that single slot, not with an attacker-controlled range of many slots. [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** ledger/src/blockstore/cleanup_service.rs (L279-288)
```rust
        if let Some(lowest_cleanup_slot) = lowest_cleanup_slot {
            *blockstore.lowest_cleanup_slot.write().unwrap() = lowest_cleanup_slot;

            let mut purge_time = Measure::start("purge_slots()");
            // purge any slots older than lowest_cleanup_slot.
            let _ = blockstore
                .purge_slots(0, lowest_cleanup_slot, PurgeType::CompactionFilter)
                .inspect_err(|e| {
                    error!("Purge failed when cleaning ledger to {lowest_cleanup_slot}: {e:?}")
                });
```

**File:** ledger/src/blockstore/blockstore_purge.rs (L125-147)
```rust
    pub(crate) fn purge_slot_cleanup_chaining(&self, slot: Slot) -> Result<()> {
        self.do_purge_slot_cleanup_chaining(slot, /* purge_alt_columns */ true)
    }

    /// Like `purge_slot_cleanup_chaining` but preserves alternate block columns.
    /// Used when switching from an alternate block to allow repair data to be retained.
    pub(crate) fn purge_slot_cleanup_chaining_keep_alt(&self, slot: Slot) -> Result<()> {
        self.do_purge_slot_cleanup_chaining(slot, /* purge_alt_columns */ false)
    }

    fn do_purge_slot_cleanup_chaining(&self, slot: Slot, purge_alt_columns: bool) -> Result<()> {
        let Some(mut slot_meta) = self.meta(slot)? else {
            return Err(BlockstoreError::SlotUnavailable);
        };
        let mut write_batch = self.get_write_batch()?;

        self.purge_range(
            &mut write_batch,
            slot,
            slot,
            PurgeType::Exact,
            purge_alt_columns,
        )?;
```

**File:** ledger/src/blockstore/blockstore_purge.rs (L310-319)
```rust
        match purge_type {
            PurgeType::Exact => self.purge_special_columns_exact(write_batch, from_slot, to_slot),
            PurgeType::CompactionFilter => {
                // Relying on the compaction filter means there is no action
                // required here. Instead, the compaction filter cleans the
                // key/value pairs in the special columns once they reach a
                // certain age. This is done to amortize the cleaning cost.
                Ok(())
            }
        }
```

**File:** ledger/src/blockstore/blockstore_purge.rs (L445-461)
```rust
    /// Purges special columns (using a non-Slot primary-index) exactly, by
    /// deserializing each slot being purged and iterating through all
    /// transactions to determine the keys of individual records.
    ///
    /// The purge range applies to \[`from_slot`, `to_slot`\].
    ///
    /// **This method is very slow.**
    fn purge_special_columns_exact(
        &self,
        batch: &mut WriteBatch,
        from_slot: Slot,
        to_slot: Slot,
    ) -> Result<()> {
        if self.special_columns_empty()? {
            return Ok(());
        }

```

**File:** ledger/src/blockstore/blockstore_purge.rs (L502-528)
```rust
    /// Send a purge request to the BlockstoreCleanupService request channel
    pub fn send_manual_purge_request(&self, max_slot_to_delete: Slot) -> Result<()> {
        // Deleting data newer than the latest root is likely to interfere
        // with replay so save any callers from themself
        let max_root = self.max_root();
        if max_slot_to_delete >= max_root {
            return Err(BlockstoreError::ManualPurge(
                BlockstoreManualPurgeError::SlotGreaterThanOrEqualToRoot {
                    request_slot: max_slot_to_delete,
                    max_root,
                },
            ));
        }

        let sender_guard = self.manual_purge_request_sender.lock().unwrap();
        let Some(ref sender) = *sender_guard else {
            return Err(BlockstoreError::ManualPurge(
                BlockstoreManualPurgeError::SenderUnavailable,
            ));
        };
        sender
            .try_send(max_slot_to_delete)
            .map_err(BlockstoreManualPurgeError::from)
            .map_err(BlockstoreError::ManualPurge)?;

        Ok(())
    }
```
