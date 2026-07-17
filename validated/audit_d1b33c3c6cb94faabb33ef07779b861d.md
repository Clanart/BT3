### Title
Missing `shard_id` Binding Check in `set_state_header` Allows Cross-Shard State Corruption During State Sync — (`File: chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a caller-supplied `shard_id` and a peer-supplied `ShardStateSyncResponseHeader`, but never verifies that the chunk embedded in the header actually belongs to the requested shard. A malicious peer can supply a valid chunk from shard B (with its valid Merkle proof) while the caller passes `shard_id = A`. The function stores the header under shard A's DB key with shard B's state root. Subsequent `set_state_part` and `apply_state_part` calls then validate and write shard B's trie data into shard A's storage, permanently corrupting the syncing node's state for shard A.

### Finding Description

In `set_state_header` the function:

1. Accepts `shard_id` as a parameter
2. Extracts the chunk from the peer-supplied `shard_state_header`
3. Validates the chunk's internal proofs via `validate_chunk_proofs`
4. Verifies the chunk is included in the block via `verify_path` against `sync_prev_block_header.chunk_headers_root()`
5. Stores the header under `StateHeaderKey(shard_id, sync_hash)` [1](#0-0) 

The Merkle proof in step 4 verifies that the chunk hash is present somewhere in the block's `chunk_headers_root` tree — it does **not** verify that the chunk occupies the leaf position corresponding to `shard_id`. The chunk's own `shard_id()` field is never compared against the `shard_id` parameter. [2](#0-1) 

After the header is accepted, `set_state_part` fetches the state root from the stored header (now shard B's root) and validates incoming parts against it: [3](#0-2) 

`validate_state_part_impl` itself also ignores `shard_id` entirely — it only validates the trie nodes against the supplied `state_root`: [4](#0-3) 

Finally, `apply_state_part` writes the validated trie changes into the storage keyed by `shard_uid` derived from the caller-supplied `shard_id` and `epoch_id`, completing the cross-shard write: [5](#0-4) 

### Impact Explanation

A syncing node that accepts a crafted `ShardStateSyncResponseHeader` from a malicious peer will:

- Store shard B's state root under shard A's `StateHeaderKey`
- Accept and store state parts that are valid for shard B (they pass `validate_state_part` because the stored root is shard B's)
- Apply shard B's full trie and flat-state delta into shard A's `ShardUId` storage slot

The result is permanent corruption of shard A's state on the syncing node. The node will subsequently produce invalid `ChunkExtra` (wrong state root), fail stateless validation checks, and be unable to participate in consensus for shard A. Recovery requires a full re-sync from scratch.

### Likelihood Explanation

State sync is the critical bootstrap path for every new or recovering validator node. Any peer the syncing node connects to can respond to state header requests. No authentication of the peer's validator status is required to serve state sync responses — the protocol relies entirely on the header validation logic in `set_state_header` to reject malformed responses. Because that validation omits the shard binding check, a single malicious (or compromised) peer that also serves the matching state parts can trigger the corruption.

### Recommendation

Add an explicit shard binding check immediately after extracting the chunk, before any other validation:

```rust
// Verify the chunk belongs to the requested shard.
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This mirrors the pattern used elsewhere in the codebase (e.g., `get_state_response_part` which explicitly checks `shard_ids.contains(&shard_id)` before proceeding). [6](#0-5) 

### Proof of Concept

1. Syncing node S requests state for shard A (`shard_id = A`) from malicious peer P.
2. P responds with a `ShardStateSyncResponseHeader` containing:
   - The chunk header for shard B (a different shard in the same block)
   - The valid Merkle proof that shard B's chunk is in the block's `chunk_headers_root`
3. S calls `set_state_header(shard_A, sync_hash, malicious_header)`.
4. `validate_chunk_proofs` passes — shard B's chunk is internally valid.
5. `verify_path(chunk_headers_root, proof_for_shard_B, chunk_B_hash)` passes — shard B's chunk is genuinely in the block.
6. **No check that `chunk.shard_id() == shard_A`.**
7. Header stored under `StateHeaderKey(shard_A, sync_hash)` with shard B's `prev_state_root`.
8. P serves state parts for shard B in response to S's part requests for shard A.
9. `set_state_part(shard_A, sync_hash, part_id, part_B)` validates `part_B` against shard B's state root — passes.
10. `apply_state_part(shard_A, state_root_B, part_id, part_B, epoch_id)` writes shard B's trie nodes and flat-state delta into shard A's `ShardUId` storage.
11. Shard A's state on node S is now shard B's state. Node S produces blocks with an invalid `prev_state_root` for shard A and is effectively broken.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L305-309)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_ids = self.epoch_manager.shard_ids(epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }
```

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
