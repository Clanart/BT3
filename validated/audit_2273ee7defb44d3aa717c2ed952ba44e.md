### Title
Right-child `CongestionInfo` computation panics on assertion when legacy-format buffered receipts exist at resharding boundary — (`chain/chain/src/resharding/manager.rs`)

---

### Summary

`get_child_congestion_info_not_finalized` computes the right child's `CongestionInfo` by subtracting the sum of all `ReceiptGroupsQueue.total_gas` values from the parent's `CongestionInfo.buffered_receipts_gas`, then asserts the result is zero. However, `CongestionInfo.buffered_receipts_gas` is always incremented when a receipt is buffered, while `ReceiptGroupsQueue` is only updated for `StateStoredReceipt::V1` receipts. Receipts buffered as plain `Receipt` (old format, `use_state_stored_receipt = false`) or as `StateStoredReceipt::V0` contribute to `CongestionInfo` but not to `ReceiptGroupsQueue`. When resharding occurs while such receipts remain in the parent shard's outgoing buffer, the assertion `assert_eq!(congestion_info.buffered_receipts_gas(), 0)` fires and panics the node.

---

### Finding Description

**The asymmetric update — the missing accrual:**

In `buffer_receipt()`, `CongestionInfo` is always updated:

```rust
self.own_congestion_info.add_receipt_bytes(size)?;
self.own_congestion_info.add_buffered_receipt_gas(gas)?;

if receipt.should_update_outgoing_metadatas() {   // ← conditional
    self.outgoing_metadatas.update_on_receipt_pushed(...)
}
```

`should_update_outgoing_metadatas()` returns `false` for both `ReceiptOrStateStoredReceipt::Receipt` and `StateStoredReceipt::V0`:

```rust
// core/primitives/src/receipt.rs
ReceiptOrStateStoredReceipt::Receipt(_) => false,
StateStoredReceipt::V0(_) => false,
StateStoredReceipt::V1(_) => true,
```

So every receipt buffered before `use_state_stored_receipt` was enabled (or before the `V1` upgrade) increments `CongestionInfo.buffered_receipts_gas` but leaves `ReceiptGroupsQueue.total_gas` at zero.

**The subtraction that assumes the invariant holds:**

`get_child_congestion_info_not_finalized` computes the right child's congestion info by subtracting the queue totals from the parent's congestion info:

```rust
for shard_id in parent_shard_layout.shard_ids() {
    let receipt_groups = ReceiptGroupsQueue::load(parent_trie, shard_id)?;
    let Some(receipt_groups) = receipt_groups else { continue; };

    let bytes = receipt_groups.total_size();
    let gas  = receipt_groups.total_gas();

    congestion_info
        .remove_buffered_receipt_gas(gas)
        .expect("Buffered gas must not exceed congestion info buffered gas");
    congestion_info
        .remove_receipt_bytes(bytes)
        .expect("Buffered size must not exceed congestion info buffered size");
}

// The right child does not inherit any buffered receipts.
assert_eq!(congestion_info.buffered_receipts_gas(), 0);  // ← panics
```

When old-format receipts are in the buffer, `ReceiptGroupsQueue.total_gas` is zero (or less than the true total), so after subtraction `congestion_info.buffered_receipts_gas()` is still non-zero, and the unconditional `assert_eq!` panics.

The codebase itself acknowledges the metadata/congestion-info split in `get_receipt_group_sizes_for_buffer_to_shard`, which explicitly handles the "metadata not fully initialized" case:

```rust
match self.outgoing_metadatas.get_metadata_for_shard(&to_shard) {
    Some(metadata) if metadata.total_receipts_num() == outgoing_receipts_buffer_len => {
        Box::new(metadata.iter_receipt_group_sizes(trie, side_effects))
    }
    _ => {
        // Metadata not initialized. Make a basic request...
        Box::new([Ok(params.max_receipt_size)].into_iter())
    }
}
```

`get_child_congestion_info_not_finalized` has no equivalent guard.

---

### Impact Explanation

The `assert_eq!` in `get_child_congestion_info_not_finalized` is a hard panic (not a debug assertion). It is reached on every resharding event via the call chain:

`start_resharding` → `split_shard` → `process_memtrie_resharding_storage_update` → `get_child_congestion_info` → `get_child_congestion_info_not_finalized`

A panic here crashes the node during the resharding block, halting block production for the affected shard. Because resharding is a protocol-level event that all validators must execute deterministically, a panic in this path can stall the entire shard split, causing a liveness failure for the affected shard.

**Impact: High** — node crash / liveness failure during resharding.

---

### Likelihood Explanation

The window is the protocol-upgrade transition period during which `use_state_stored_receipt` (or `StateStoredReceipt::V1`) is newly enabled but old-format receipts remain in outgoing buffers. If a resharding event is scheduled to coincide with or shortly follow such an upgrade, the panic is triggered. Resharding is increasingly common with dynamic resharding enabled, and the transition window can span multiple epochs depending on buffer drain rate.

---

### Recommendation

In `get_child_congestion_info_not_finalized`, replace the hard assertion with a guard that handles the partially-initialized metadata case. Two options:

1. **Fallback to `bootstrap_congestion_info`**: if `ReceiptGroupsQueue` totals do not account for all buffered gas, recompute the right child's congestion info by iterating the actual buffered receipts (as `bootstrap_congestion_info` does).

2. **Soft check**: replace `assert_eq!` with a saturating subtraction and a warning log, accepting that the right child's initial `buffered_receipts_gas` may be slightly over-estimated until the old receipts drain.

The fix should mirror the existing guard in `get_receipt_group_sizes_for_buffer_to_shard` that already handles the "metadata not fully initialized" case.

---

### Proof of Concept

1. Protocol version N: shard P buffers cross-shard receipts to shard Q as plain `Receipt` (old format). `CongestionInfo.buffered_receipts_gas` = G > 0. `ReceiptGroupsQueue.total_gas` for Q = 0.

2. Protocol version N+1: `use_state_stored_receipt` is enabled. New receipts are stored as `StateStoredReceipt::V1`. Old receipts remain in the buffer.

3. A resharding event splits shard P into P_left and P_right.

4. `get_child_congestion_info_not_finalized` is called for P_right (`RetainMode::Right`):
   - Loads `ReceiptGroupsQueue` for each shard in parent layout → total gas = 0 (old receipts not tracked).
   - Subtracts 0 from `CongestionInfo.buffered_receipts_gas` = G.
   - Reaches `assert_eq!(congestion_info.buffered_receipts_gas(), 0)` with value G > 0.
   - **Panics.**

5. The resharding block cannot be processed; the node crashes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L465-501)
```rust
    /// Put a receipt in the outgoing receipt buffer of a shard.
    fn buffer_receipt(
        &mut self,
        receipt: Receipt,
        size: u64,
        gas: Gas,
        state_update: &mut TrieUpdate,
        shard: ShardId,
        use_state_stored_receipt: bool,
    ) -> Result<(), RuntimeError> {
        let receipt = match use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_owned(receipt, metadata);
                let receipt = ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt);
                receipt
            }
            false => ReceiptOrStateStoredReceipt::Receipt(std::borrow::Cow::Owned(receipt)),
        };

        self.own_congestion_info.add_receipt_bytes(size)?;
        self.own_congestion_info.add_buffered_receipt_gas(gas)?;

        if receipt.should_update_outgoing_metadatas() {
            self.outgoing_metadatas.update_on_receipt_pushed(
                shard,
                ByteSize::b(size),
                gas,
                state_update,
            )?;
        }

        self.outgoing_buffers.to_shard(shard).push_back(state_update, &receipt)?;
        self.stats.buffered_receipts.entry(shard).or_default().add_receipt(size, gas);
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L571-607)
```rust
    fn get_receipt_group_sizes_for_buffer_to_shard<'a>(
        &'a self,
        to_shard: ShardId,
        trie: &'a dyn TrieAccess,
        side_effects: bool,
        params: &BandwidthSchedulerParams,
    ) -> Box<dyn Iterator<Item = Result<u64, StorageError>> + 'a> {
        let outgoing_receipts_buffer_len = self.outgoing_buffers.buffer_len(to_shard).unwrap_or(0);

        if outgoing_receipts_buffer_len == 0 {
            // No receipts in the outgoing buffer, return an empty iterator.
            return Box::new(std::iter::empty());
        }

        // To make a proper bandwidth request we need the metadata for the outgoing buffer to be fully initialized
        // (i.e. contain data about all of the receipts in the outgoing buffer). There is a moment right after the
        // protocol upgrade where the outgoing buffer contains receipts which were buffered in the previous protocol
        // version where metadata was not enabled. Metadata doesn't contain information about them.
        // We can't make a proper request in this case, so we make a basic request while we wait for
        // metadata to become fully initialized. The basic request requests just `max_receipt_size`. This is enough to
        // ensure liveness, as all receipts are smaller than `max_receipt_size`. The resulting behavior is similar
        // to the previous approach where the `allowed_shard` was assigned most of the bandwidth.
        // Over time these old receipts will be removed from the outgoing buffer and eventually metadata will contain
        // information about every receipt in the buffer. From that point on we will be able to make
        // proper bandwidth requests.

        match self.outgoing_metadatas.get_metadata_for_shard(&to_shard) {
            Some(metadata) if metadata.total_receipts_num() == outgoing_receipts_buffer_len => {
                // Metadata fully initialized, use it to read receipt group sizes.
                Box::new(metadata.iter_receipt_group_sizes(trie, side_effects))
            }
            _ => {
                // Metadata not initialized. Make a basic request which requests only `max_receipt_size`.
                Box::new([Ok(params.max_receipt_size)].into_iter())
            }
        }
    }
```

**File:** core/primitives/src/receipt.rs (L169-217)
```rust
    pub fn should_update_outgoing_metadatas(&self) -> bool {
        match self {
            ReceiptOrStateStoredReceipt::Receipt(_) => false,
            ReceiptOrStateStoredReceipt::StateStoredReceipt(state_stored_receipt) => {
                state_stored_receipt.should_update_outgoing_metadatas()
            }
        }
    }
}

impl<'a> StateStoredReceipt<'a> {
    pub fn new_owned(receipt: Receipt, metadata: StateStoredReceiptMetadata) -> Self {
        let receipt = Cow::Owned(receipt);
        Self::V1(StateStoredReceiptV1 { receipt, metadata })
    }

    pub fn new_borrowed(receipt: &'a Receipt, metadata: StateStoredReceiptMetadata) -> Self {
        let receipt = Cow::Borrowed(receipt);

        Self::V1(StateStoredReceiptV1 { receipt, metadata })
    }

    pub fn into_receipt(self) -> Receipt {
        match self {
            StateStoredReceipt::V0(v0) => v0.receipt.into_owned(),
            StateStoredReceipt::V1(v1) => v1.receipt.into_owned(),
        }
    }

    pub fn get_receipt(&self) -> &Receipt {
        match self {
            StateStoredReceipt::V0(v0) => &v0.receipt,
            StateStoredReceipt::V1(v1) => &v1.receipt,
        }
    }

    pub fn metadata(&self) -> &StateStoredReceiptMetadata {
        match self {
            StateStoredReceipt::V0(v0) => &v0.metadata,
            StateStoredReceipt::V1(v1) => &v1.metadata,
        }
    }

    pub fn should_update_outgoing_metadatas(&self) -> bool {
        match self {
            StateStoredReceipt::V0(_) => false,
            StateStoredReceipt::V1(_) => true,
        }
    }
```

**File:** chain/chain/src/resharding/manager.rs (L327-365)
```rust
    fn get_child_congestion_info_not_finalized(
        parent_trie: &dyn TrieAccess,
        parent_shard_layout: &ShardLayout,
        parent_congestion_info: CongestionInfo,
        retain_mode: RetainMode,
    ) -> Result<CongestionInfo, Error> {
        // The left child contains all the delayed and buffered receipts from the
        // parent so it should have identical congestion info.
        if retain_mode == RetainMode::Left {
            return Ok(parent_congestion_info);
        }

        // The right child contains all the delayed receipts from the parent but it
        // has no buffered receipts. It's info needs to be computed by subtracting
        // the parent's buffered receipts from the parent's congestion info.
        let mut congestion_info = parent_congestion_info;
        for shard_id in parent_shard_layout.shard_ids() {
            let receipt_groups = ReceiptGroupsQueue::load(parent_trie, shard_id)?;
            let Some(receipt_groups) = receipt_groups else {
                continue;
            };

            let bytes = receipt_groups.total_size();
            let gas = receipt_groups.total_gas();

            congestion_info
                .remove_buffered_receipt_gas(gas)
                .expect("Buffered gas must not exceed congestion info buffered gas");
            congestion_info
                .remove_receipt_bytes(bytes)
                .expect("Buffered size must not exceed congestion info buffered size");
        }

        // The right child does not inherit any buffered receipts. The
        // congestion info must match this invariant.
        assert_eq!(congestion_info.buffered_receipts_gas(), 0);

        Ok(congestion_info)
    }
```

**File:** core/primitives/src/congestion_info.rs (L309-321)
```rust
    pub fn remove_buffered_receipt_gas(&mut self, gas: u128) -> Result<(), RuntimeError> {
        match self {
            CongestionInfo::V1(inner) => {
                inner.buffered_receipts_gas =
                    inner.buffered_receipts_gas.checked_sub(gas).ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow(
                            "remove_buffered_receipt_gas".into(),
                        )
                    })?;
            }
        }
        Ok(())
    }
```
