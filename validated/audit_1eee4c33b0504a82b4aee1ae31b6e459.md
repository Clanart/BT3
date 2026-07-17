### Title
Missing `shard_id` binding check in `set_state_header` allows a malicious peer to store a wrong-shard state header under an arbitrary shard's key — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a caller-supplied `shard_id` and a `ShardStateSyncResponseHeader` from a peer, but never verifies that the chunk embedded in the header actually belongs to `shard_id`. A malicious peer can serve a header for shard N in response to a request for shard M. All existing checks pass, and the wrong-shard header is persisted under `StateHeaderKey(shard_id=M, sync_hash)`. Subsequent `set_state_part` calls for shard M then validate parts against shard N's `state_root` and write them to shard M's DB slot, causing the node to apply shard N's trie state into shard M's storage.

---

### Finding Description

`set_state_header` in `chain/chain/src/state_sync/adapter.rs` performs five validation steps on the incoming `ShardStateSyncResponseHeader`:

1. `validate_chunk_proofs` — verifies internal chunk hash consistency (transactions, receipts root). Does **not** check `chunk.shard_id()`.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk_hash, height_included))` — verifies the chunk is somewhere in the block's Merkle tree. The proof is position-agnostic: a chunk from shard N at index N passes this check even when the caller expects shard M.
3. `verify_path` for `prev_chunk` — same issue.
4. Receipt proof loop — uses the caller-supplied `shard_id` to hash receipts (`ReceiptList(shard_id, receipts)`). This would catch a mismatch **only if** `incoming_receipts_proofs` is non-empty. When the attacker supplies a header for a shard with zero incoming cross-shard receipts, the loop body never executes.
5. `validate_state_root_node` — validates the state root node against `chunk_inner.prev_state_root()`. This is self-consistent within the wrong-shard header and passes.

After all checks pass, the header is stored:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

`shard_id` here is the caller-supplied parameter, not `chunk.shard_id()`. The stored header contains shard N's chunk and `prev_state_root`. [1](#0-0) 

`set_state_part` then retrieves this header by `(shard_id=M, sync_hash)`, extracts shard N's `state_root`, and validates incoming parts against it:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// state_root is now shard N's root, not shard M's
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
``` [2](#0-1) 

`validate_state_part_impl` does **not** use `shard_id` in its cryptographic check at all — it only calls `Trie::validate_state_part(state_root, part_id, partial_state)`: [3](#0-2) 

Parts that are valid for shard N's trie pass validation and are stored under `StatePartKey(sync_hash, shard_id=M, part_id)`. `apply_state_part` then writes shard N's trie nodes into shard M's `ShardUId` slot. [4](#0-3) 

---

### Impact Explanation

A syncing node that accepts a state header from a malicious peer will apply the wrong shard's trie state. After `set_state_finalize`, the node's shard M has shard N's account/contract/balance data. All subsequent block processing for shard M produces incorrect results (wrong state roots, failed transactions, incorrect balances). The node diverges from the canonical chain and is effectively corrupted for the duration of its operation.

**Impact: High** — permanent state corruption of the syncing node for the targeted shard.

---

### Likelihood Explanation

State sync headers are downloaded from arbitrary peers. Any peer in the NEAR P2P network can respond to `StateRequestHeader` messages. No privileged role is required. The attack is most reliable when targeting a shard with zero incoming cross-shard receipts in the epoch (the receipt-proof loop is skipped entirely), which is a normal condition in low-traffic epochs or single-shard-active scenarios.

**Likelihood: High** — unprivileged network peer, no special conditions beyond finding a shard with empty incoming receipts.

---

### Recommendation

Add an explicit `shard_id` binding check immediately after extracting the chunk from the header, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
// Bind the chunk's shard_id to the requested shard_id
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} does not match requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

This mirrors the pattern already used in `validate_block_impl`, which explicitly checks `chunk_header.shard_id() != shard_id` for each chunk position: [5](#0-4) 

---

### Proof of Concept

1. Syncing node S requests `StateRequestHeader { shard_id: 0, sync_hash }` from peer P.
2. Malicious peer P responds with a `ShardStateSyncResponseHeader` containing shard 1's chunk (which has no incoming receipts in this epoch) and a valid Merkle proof showing shard 1's chunk is in the block.
3. S calls `set_state_header(shard_id=0, sync_hash, header_for_shard_1)`.
4. `validate_chunk_proofs` passes (shard 1's chunk is internally consistent).
5. `verify_path(chunk_headers_root, proof, ChunkHashHeight(shard1_chunk_hash, height))` passes (shard 1's chunk IS in the block).
6. Receipt proof loop: `incoming_receipts_proofs` is empty → loop body never executes → no shard_id mismatch detected.
7. `validate_state_root_node` passes (self-consistent within shard 1's header).
8. Header stored under `StateHeaderKey(shard_id=0, sync_hash)` with shard 1's `prev_state_root`.
9. S calls `set_state_part(shard_id=0, sync_hash, part_id, part_for_shard_1)`.
10. `get_state_header(0, sync_hash)` returns shard 1's header → `state_root` = shard 1's root.
11. `validate_state_part(shard_id=0, shard1_state_root, part_id, part)` passes (part is valid for shard 1's trie).
12. Part stored under `StatePartKey(sync_hash, shard_id=0, part_id)`.
13. `apply_state_part(shard_id=0, shard1_state_root, ...)` writes shard 1's trie nodes into shard 0's `ShardUId` storage.
14. Node S now has shard 1's state in shard 0's slot — permanent state corruption. [6](#0-5) [7](#0-6)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-403)
```rust
    pub fn set_state_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        shard_state_header: ShardStateSyncResponseHeader,
    ) -> Result<(), Error> {
        let sync_block_header = self.chain_store.get_block_header(&sync_hash)?;

        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }

        // Consider chunk itself is valid.

        // 3. Checking that chunks `chunk` and `prev_chunk` are included in appropriate blocks
        // 3a. Checking that chunk `chunk` is included into block at last height before sync_hash
        // 3aa. Also checking chunk.height_included
        let sync_prev_block_header =
            self.chain_store.get_block_header(sync_block_header.prev_hash())?;
        if !verify_path(
            *sync_prev_block_header.chunk_headers_root(),
            shard_state_header.chunk_proof(),
            &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk isn't included into block".into(),
            ));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-560)
```rust
    pub fn set_state_part(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        part_id: PartId,
        part: &StatePart,
    ) -> Result<(), Error> {
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
            StatePartValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(format!(
                "set_state_part failed: validate_state_part failed. state_root={:?}",
                state_root
            )));
        }
        // Saving the part data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id.idx)).unwrap();
        let bytes = part.to_bytes();
        store_update.set(DBCol::StateParts, &key, &bytes);
        store_update.commit();
        Ok(())
```

**File:** chain/chain/src/runtime/mod.rs (L531-551)
```rust
    fn validate_state_part_impl(
        &self,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
    ) -> StatePartValidationResult {
        let partial_state = part.to_partial_state();
        let Ok(partial_state) = part.to_partial_state() else {
            // Deserialization error means we've got the data from malicious peer
            tracing::error!(target: "state-parts", ?partial_state, "state part deserialization error");
            return StatePartValidationResult::Invalid;
        };
        match Trie::validate_state_part(state_root, part_id, partial_state) {
            Ok(_) => StatePartValidationResult::Valid,
            // Storage error should not happen
            Err(err) => {
                tracing::error!(target: "state-parts", ?err, "state part storage error");
                StatePartValidationResult::Invalid
            }
        }
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1501-1528)
```rust
    fn apply_state_part(
        &self,
        shard_id: ShardId,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
        epoch_id: &EpochId,
    ) -> Result<(), Error> {
        let _timer = metrics::STATE_SYNC_APPLY_PART_DELAY
            .with_label_values(&[&shard_id.to_string()])
            .start_timer();

        let part = part
            .to_partial_state()
            .expect("Part was already validated earlier, so could never fail here");
        let ApplyStatePartResult { trie_changes, flat_state_delta, contract_codes } =
            Trie::apply_state_part(state_root, part_id, part);
        let tries = self.get_tries();
        let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
        let mut store_update = tries.store_update();
        tries.apply_all(&trie_changes, shard_uid, &mut store_update);
        tracing::debug!(target: "chain", %shard_id, values_count = %flat_state_delta.len(), "inserting values to flat storage");
        // TODO: `apply_to_flat_state` inserts values with random writes, which can be time consuming.
        //       Optimize taking into account that flat state values always correspond to a consecutive range of keys.
        flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
        self.precompile_contracts(epoch_id, contract_codes)?;
        store_update.commit();
        Ok(())
```

**File:** chain/chain/src/chain.rs (L799-802)
```rust
            } else if chunk_header.is_new_chunk() {
                if chunk_header.shard_id() != shard_id {
                    return Err(Error::InvalidShardId(chunk_header.shard_id()));
                }
```
