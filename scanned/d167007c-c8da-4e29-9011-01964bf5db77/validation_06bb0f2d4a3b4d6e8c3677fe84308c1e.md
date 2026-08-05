### Title
Blockstore FIFO cleanup accounts only for shred count, allowing unbounded growth of the non-slot-keyed `TransactionStatus`/`TransactionMemos`/`AddressSignatures` columns - (File: `ledger/src/blockstore/cleanup_service.rs`, `ledger/src/blockstore/blockstore_purge.rs`)

### Summary
The external report describes a database with no bounded-growth/rollback safety net: unvalidated writes can exhaust key space and there is no application-level mechanism to keep storage bounded except a hand-rolled deletion. Agave's `Blockstore` has an analogous gap: its only automatic disk-bounding mechanism, `BlockstoreCleanupService`, sizes its cleanup trigger exclusively off data/coding **shred** counts, while the address/transaction-metadata column families (which are *not* keyed by slot as the primary index) are excluded from the fast range-delete path and only reclaimed lazily through a RocksDB compaction filter. Because the trigger metric (shred count) is decoupled from the actual size driver of these columns (number of account keys × number of transactions), a validator that has `enable_rpc_transaction_history` on can have these columns grow disproportionately to the shred-based threshold, and even once cleanup is triggered, reclamation is deferred to best-effort background compaction rather than immediate deletion.

### Finding Description
`BlockstoreCleanupService` triggers cleanup purely based on `num_data_shreds`/`num_coding_shreds` versus `DEFAULT_MAX_BLOCKSTORE_SHREDS` (400,000,000) or a configured `BlockstoreCleanupStrategy` limit: [1](#0-0) 

The strategy comment explicitly documents that with `BlockstoreCleanupStrategy::None` "The `Blockstore` will be allowed to grow without bound in size": [2](#0-1) 

Even when cleanup runs, it purges via `PurgeType::CompactionFilter`, and `purge_range` explicitly does nothing for the special columns in that mode - it defers entirely to RocksDB's compaction filter to eventually drop old key/value pairs: [3](#0-2) [4](#0-3) 

The columns affected - `TransactionStatus`, `TransactionMemos`, `AddressSignatures` - are keyed by signature/pubkey rather than slot, so they cannot be range-deleted like the other slot-indexed columns and are called out as needing special handling to "avoid unbounded storage growth": [5](#0-4) 

Critically, `AddressSignatures` records one entry **per account key per transaction**, not one entry per shred/byte: [6](#0-5) 

This creates a metric mismatch: the cleanup trigger (`num_data_shreds`/`num_coding_shreds`) measures wire-level shred volume, but the actual disk consumer in these three columns scales with `transactions × account_keys_per_transaction`. A transaction that maximizes account keys (e.g. via address-lookup tables) inflates `AddressSignatures` row count and `TransactionStatus`/`TransactionMemos` payload size (loaded addresses, logs, inner instructions) without a proportional increase in shred count, since shred count is bounded by the compact serialized entry bytes, not the semantic contents recorded per key. The result is that these columns can grow well past what the shred-based watermark models, and once a purge is finally requested, reclamation for these columns is not immediate (unlike the range-delete used for all other columns) - it is delayed until RocksDB's background compaction visits the relevant SST files under `PurgedSlotFilter`.

### Impact Explanation
On any node running with transaction history enabled (a common RPC-node configuration, not requiring any privileged access), an attacker who simply submits ordinary, fee-paying transactions with many static/loaded account keys can cause the `TransactionStatus`/`TransactionMemos`/`AddressSignatures` column families to consume disk space disproportionately to what the shred-based `BlockstoreCleanupService` threshold anticipates, and that excess is not promptly reclaimed even after cleanup fires because it depends on background compaction rather than an immediate delete. Sustained abuse degrades or exhausts disk on affected validators/RPC nodes, matching the class of "non-RPC remote exhaustion/crash" impact - no malicious peer, admin, or leaked key is required, only ordinary transaction submission at scale.

### Likelihood Explanation
The write path (`write_transaction_status`) is invoked from ordinary block processing whenever transaction history is enabled, so it is reachable by any transaction submitted to the cluster - it does not require RPC access to the specific node being harmed. The likelihood of the underlying design gap (shred-count-only trigger, non-immediate compaction-based reclamation for the special columns) is confirmed directly in code comments and structure, but I was not able to fully quantify from local code alone the real-world ratio between "shreds consumed" and "special-column bytes consumed" per transaction (e.g., exact per-key overhead, compaction cadence under load), so the precise time-to-exhaustion is uncertain without a running benchmark.

### Recommendation
- Track disk usage independently for `TransactionStatus`, `TransactionMemos`, and `AddressSignatures` (e.g., via periodic size estimation or entry counts) rather than relying solely on shred counts to gate cleanup.
- Consider triggering `PurgeType::Exact` or an eager delete pass for these columns when they exceed independent thresholds, instead of relying entirely on compaction-filter timing.
- Add configurable caps on the number of account keys credited to `AddressSignatures` per transaction/slot to bound worst-case per-transaction storage amplification.

### Proof of Concept
Conceptual, not exploited end-to-end from local code alone:
1. Run a validator/RPC node with `enable_rpc_transaction_history` enabled (writes go through `write_transaction_status`, which fans out to `TransactionStatus`, `TransactionMemos`, and one `AddressSignatures` row per account key per tx, as shown in `purge_special_columns_exact`'s deletion loop at [6](#0-5) ).
2. Repeatedly submit ordinary fee-paying transactions that reference the maximum practical number of account keys per transaction (e.g., via address-lookup tables), keeping shred bytes/tx close to typical while maximizing `AddressSignatures` row fan-out and `TransactionStatus`/`TransactionMemos` payload size.
3. Observe that `BlockstoreCleanupService`'s shred-count watermark (`DEFAULT_MAX_BLOCKSTORE_SHREDS`) does not reflect this disproportionate growth (`ledger/src/blockstore/cleanup_service.rs:206-219`), and that once a purge is eventually requested via `PurgeType::CompactionFilter`, the special columns are not immediately reclaimed (`blockstore_purge.rs:300-320`) but instead depend on RocksDB compaction cadence, allowing sustained accumulation under continued attacker traffic.

### Citations

**File:** ledger/src/blockstore/cleanup_service.rs (L206-219)
```rust
        let (num_shreds, max_num_shreds) = match cleanup_strategy {
            BlockstoreCleanupStrategy::None => {
                // Automatic blockstore cleanup is disabled
                return;
            }
            BlockstoreCleanupStrategy::CountDataShreds(limit) => (num_data_shreds, limit),
            BlockstoreCleanupStrategy::CountDataAndCodingShreds(limit) => {
                (num_data_shreds + num_coding_shreds, limit)
            }
        };
        if num_shreds <= max_num_shreds {
            // Cleanup is not necessary at this time
            return;
        }
```

**File:** ledger/src/blockstore/cleanup_service.rs (L279-303)
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
            // Update only after purge operation.
            // Safety: This value can be used by compaction_filters shared via Arc<AtomicU64>.
            // Compactions are async and run as a multi-threaded background job. However, this
            // shouldn't cause consistency issues for iterators and getters because we have
            // already expired all affected keys (older than or equal to lowest_cleanup_slot)
            // by the above `purge_slots`. According to the general RocksDB design where SST
            // files are immutable, even running iterators aren't affected; the database grabs
            // a snapshot of the live set of sst files at iterator's creation.
            // Also, we passed the PurgeType::CompactionFilter, meaning no delete_range for
            // transaction_status and address_signatures CFs. These are fine because they
            // don't require strong consistent view for their operation.
            blockstore.set_max_expired_slot(lowest_cleanup_slot);
            purge_time.stop();
            info!("Cleaned up Blockstore data older than slot {lowest_cleanup_slot}. {purge_time}");
        }
```

**File:** ledger/src/blockstore_options.rs (L138-153)
```rust
/// Control how `BlockstoreCleanupService` will decide when to perform cleanup
#[derive(Clone, Copy, Debug)]
pub enum BlockstoreCleanupStrategy {
    /// No cleanup strategy
    ///
    /// The `Blockstore` will be allowed to grow without bound in size
    None,
    /// Count the number of data shreds in the `Blockstore`
    ///
    /// Data is purged when the specified capacity is reached/exceeded
    CountDataShreds(u64),
    /// Count the number of data and coding shreds in the `Blockstore`
    ///
    /// Data is purged when the specified capacity is reached/exceeded
    CountDataAndCodingShreds(u64),
}
```

**File:** ledger/src/blockstore/blockstore_purge.rs (L300-320)
```rust
        } else {
            // This column stores information for both the original and alternate
            // locations. When `purge_alt_columns` is not specified we only delete the
            // data associated with the original column.
            for slot in from_slot..=to_slot {
                self.double_merkle_meta_cf
                    .delete_in_batch(write_batch, (slot, BlockLocation::Original));
            }
        }

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
    }
```

**File:** ledger/src/blockstore/blockstore_purge.rs (L480-491)
```rust
                    let account_keys = AccountKeys::new(
                        transaction.message.static_account_keys(),
                        loaded_addresses.as_ref(),
                    );

                    let transaction_index =
                        u32::try_from(i).map_err(|_| BlockstoreError::TransactionIndexOverflow)?;
                    for pubkey in account_keys.iter() {
                        self.address_signatures_cf
                            .delete_in_batch(batch, (*pubkey, slot, transaction_index, signature));
                    }
                }
```

**File:** ledger/src/blockstore_db.rs (L1180-1204)
```rust
// Returns whether compactions should be enabled for the given column (name).
fn should_enable_cf_compaction(cf_name: &str) -> bool {
    // In order to keep the ledger storage footprint within a desired size,
    // LedgerCleanupService removes data in FIFO order by slot.
    //
    // Several columns do not contain slot in their key. These columns must
    // be manually managed to avoid unbounded storage growth.
    //
    // Columns where slot is the primary index can be efficiently cleaned via
    // Database::delete_range_cf() && Database::delete_file_in_range_cf().
    //
    // Columns where a slot is part of the key but not the primary index can
    // not be range deleted like above. Instead, the individual key/value pairs
    // must be iterated over and a decision to keep or discard that pair is
    // made. The comparison logic is implemented in PurgedSlotFilter which is
    // configured to run as part of rocksdb's automatic compactions. Storage
    // space is reclaimed on this class of columns once compaction has
    // completed on a given range or file.
    matches!(
        cf_name,
        columns::TransactionStatus::NAME
            | columns::TransactionMemos::NAME
            | columns::AddressSignatures::NAME
    )
}
```
