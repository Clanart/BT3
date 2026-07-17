### Title
`set_state_header` Does Not Bind the Supplied `shard_id` to the Chunk Inside the Header — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`StateSyncAdapter::set_state_header` accepts a caller-supplied `shard_id` and a peer-supplied `ShardStateSyncResponseHeader`. It verifies that the chunk inside the header is included in the block (via a merkle path against `chunk_headers_root`), but it never checks that the chunk's own `shard_id()` equals the caller-supplied `shard_id`. A malicious peer can therefore respond to a state-sync header request for shard Y with a valid header whose embedded chunk belongs to shard X. The header passes all existing checks and is stored under `StateHeaderKey(shard_Y, sync_hash)`. Every subsequent step — `set_state_part`, `apply_state_part`, and `set_state_finalize` — then reconstructs shard Y's trie and flat storage from shard X's state root, permanently corrupting the syncing node's state for shard Y.

---

### Finding Description

`set_state_header` performs five checks on the incoming header:

1. Internal chunk proofs (`validate_chunk_proofs`) [1](#0-0) 
2. Merkle inclusion of the chunk in the block's `chunk_headers_root` [2](#0-1) 
3. Merkle inclusion of `prev_chunk` in the preceding block [3](#0-2) 
4. Validity of incoming receipt proofs [4](#0-3) 
5. Validity of the state root node [5](#0-4) 

None of these checks compares `chunk.shard_id()` against the caller-supplied `shard_id`. The merkle proof in step 2 uses `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` as the leaf value. The `chunk_headers_root` is a tree over **all** shards' chunks; the proof only establishes that the chunk (by hash) is somewhere in the tree, not that it occupies the position corresponding to `shard_id`. A chunk from shard X has a valid proof of inclusion in the same block, so the check passes even when `shard_id` is set to Y.

After passing validation the header is stored under the wrong key:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [6](#0-5) 

`set_state_part` then retrieves this header by `(shard_Y, sync_hash)`, extracts the state root from shard X's chunk, and validates incoming parts against that root:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// validates part against shard-X's state_root, not shard-Y's
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
``` [7](#0-6) 

`validate_state_part_impl` does not use `shard_id` at all — it only checks the trie against `state_root`: [8](#0-7) 

`apply_state_part` then writes the resulting trie changes and flat-state delta into `shard_uid` derived from the caller-supplied `shard_id` (Y), not from the chunk's actual shard (X):

```rust
let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
tries.apply_all(&trie_changes, shard_uid, &mut store_update);
flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
``` [9](#0-8) 

The result is that shard Y's on-disk trie and flat storage are populated with shard X's state.

---

### Impact Explanation

A syncing node that accepts a cross-shard header will reconstruct shard Y's state from shard X's trie data. After `set_state_finalize` completes, the node believes it has a valid state for shard Y but actually holds shard X's account balances, contract code, and storage. Any chunk the node subsequently produces for shard Y will be based on this corrupted state, causing it to be rejected by validators and potentially triggering slashing. The node cannot self-heal without a full re-sync. In a multi-shard network this can be triggered independently for each shard the node tracks.

---

### Likelihood Explanation

Any network peer can respond to a `StateRequestHeader` message. The `StateRequestActor` only validates that `sync_hash` belongs to a known recent epoch; it does not validate the shard identity of the returned header: [10](#0-9) 

A single malicious peer that is reachable during state sync can deliver the crafted header. The state sync downloader retries on failure but accepts the first successful response, so one successful delivery is sufficient: [11](#0-10) 

---

### Recommendation

Add an explicit shard-identity check immediately after extracting the chunk from the header in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: bind the chunk's shard to the requested shard_id
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: header chunk shard_id {:?} != requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

This mirrors the existing shard-ID checks performed in state-witness validation (e.g., `spice_pre_validate_chunk_state_witness` verifies `shard_layout.shard_ids().contains(&shard_id)` and then indexes the block's chunks by that shard): [12](#0-11) 

---

### Proof of Concept

1. Node B begins state sync for shard Y against `sync_hash`.
2. Malicious peer M intercepts the `StateRequestHeader { shard_id: Y, sync_hash }` request.
3. M responds with a `ShardStateSyncResponseHeader` whose embedded `chunk` is the legitimate chunk for shard X at the same block, together with a valid merkle proof of that chunk's inclusion in the block's `chunk_headers_root`.
4. Node B calls `set_state_header(shard_Y, sync_hash, header_from_M)`.
5. `validate_chunk_proofs` passes (chunk X's internal proofs are valid). [13](#0-12) 
6. `verify_path(chunk_headers_root, proof, ChunkHashHeight(chunk_X_hash, height))` passes (chunk X is in the block). [2](#0-1) 
7. No check compares `chunk.shard_id()` (= X) with `shard_id` (= Y). The header is stored under `StateHeaderKey(Y, sync_hash)`.
8. Node B downloads state parts for shard X (which validate against shard X's `state_root`) and calls `set_state_part(shard_Y, sync_hash, part_id, part)`. Each part passes `validate_state_part` because the state root extracted from the stored header is shard X's root. [7](#0-6) 
9. `apply_state_part(shard_Y, state_root_X, ...)` writes shard X's trie nodes and flat-state entries into `ShardUId` for shard Y. [14](#0-13) 
10. `set_state_finalize(shard_Y, sync_hash)` completes without error. Node B now has shard X's state installed as shard Y's state. [15](#0-14)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L379-385)
```rust
        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L394-403)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L412-435)
```rust
        match (&prev_chunk_header, shard_state_header.prev_chunk_proof()) {
            (Some(prev_chunk_header), Some(prev_chunk_proof)) => {
                let prev_block_header =
                    self.chain_store.get_block_header(block_header.prev_hash())?;
                if !verify_path(
                    *prev_block_header.chunk_headers_root(),
                    prev_chunk_proof,
                    &ChunkHashHeight(prev_chunk_header.chunk_hash().clone(), prev_chunk_header.height_included()),
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other(
                        "set_shard_state failed: prev_chunk isn't included into block".into(),
                    ));
                }
            }
            (None, None) => {
                if chunk.height_included() != 0 {
                    return Err(Error::Other(
                    "set_shard_state failed: received empty state response for a chunk that is not at height 0".into()
                ));
                }
            }
            _ =>
                return Err(Error::Other("set_shard_state failed: `prev_chunk_header` and `prev_chunk_proof` must either both be present or both absent".into()))
```

**File:** chain/chain/src/state_sync/adapter.rs (L438-510)
```rust
        // 4. Proving incoming receipts validity
        // 4a. Checking len of proofs
        if shard_state_header.root_proofs().len()
            != shard_state_header.incoming_receipts_proofs().len()
        {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
        }
        let mut hash_to_compare = sync_hash;
        for (i, receipt_response) in
            shard_state_header.incoming_receipts_proofs().iter().enumerate()
        {
            let ReceiptProofResponse(block_hash, receipt_proofs) = receipt_response;

            // 4b. Checking that there is a valid sequence of continuous blocks
            if *block_hash != hash_to_compare {
                byzantine_assert!(false);
                return Err(Error::Other(
                    "set_shard_state failed: invalid incoming receipts".into(),
                ));
            }
            let header = self.chain_store.get_block_header(&hash_to_compare)?;
            hash_to_compare = *header.prev_hash();

            let block_header = self.chain_store.get_block_header(block_hash)?;
            // 4c. Checking len of receipt_proofs for current block
            if receipt_proofs.len() != shard_state_header.root_proofs()[i].len()
                || receipt_proofs.len() != block_header.chunks_included() as usize
            {
                byzantine_assert!(false);
                return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
            }
            // We know there were exactly `block_header.chunks_included` chunks included
            // on the height of block `block_hash`.
            // There were no other proofs except for included chunks.
            // According to Pigeonhole principle, it's enough to ensure all receipt_proofs are distinct
            // to prove that all receipts were received and no receipts were hidden.
            let mut visited_shard_ids = HashSet::<ShardId>::new();
            for (j, receipt_proof) in receipt_proofs.iter().enumerate() {
                let ReceiptProof(receipts, shard_proof) = receipt_proof;
                let ShardProof { from_shard_id, to_shard_id: _, proof } = shard_proof;
                // 4d. Checking uniqueness for set of `from_shard_id`
                match visited_shard_ids.get(from_shard_id) {
                    Some(_) => {
                        byzantine_assert!(false);
                        return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                    }
                    _ => visited_shard_ids.insert(*from_shard_id),
                };
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
                // 4f. Proving the outgoing_receipts_root matches that in the block
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
            }
        }
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L512-523)
```rust
        // 5. Checking that state_root_node is valid
        let chunk_inner = chunk.take_header().take_inner();
        if matches!(
            self.runtime_adapter.validate_state_root_node(
                shard_state_header.state_root_node(),
                chunk_inner.prev_state_root(),
            ),
            StateRootNodeValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: state_root_node is invalid".into()));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/state_sync/adapter.rs (L541-553)
```rust
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

**File:** chain/chain/src/runtime/mod.rs (L1516-1525)
```rust
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
```

**File:** chain/client/src/state_request_actor.rs (L139-174)
```rust
    /// Validates sync hash and returns appropriate action to take.
    fn validate_sync_hash(&self, sync_hash: &CryptoHash) -> SyncHashValidationResult {
        match self.is_sync_hash_from_known_recent_epoch(sync_hash) {
            Ok(true) => {}
            Ok(false) => {
                tracing::info!(
                    target: "sync",
                    ?sync_hash,
                    "sync_hash didn't pass validation; belongs to an unknown epoch"
                );
                return SyncHashValidationResult::Rejected;
            }
            Err(err) => {
                tracing::warn!(target: "sync", ?err, "failed to check sync_hash epoch");
                return SyncHashValidationResult::Rejected;
            }
        }

        let good_sync_hash = match self.get_sync_hash(sync_hash) {
            Ok(sync_hash) => sync_hash,
            Err(err) => {
                tracing::debug!(target: "sync", ?err, "failed to get sync_hash for state request");
                return SyncHashValidationResult::Rejected;
            }
        };

        if good_sync_hash.as_ref() == Some(sync_hash) {
            SyncHashValidationResult::Valid
        } else {
            tracing::warn!(
                target: "sync",
                "sync_hash didn't pass validation; possible divergence in sync hash computation"
            );
            SyncHashValidationResult::Rejected
        }
    }
```

**File:** chain/client/src/sync/state/downloader.rs (L164-192)
```rust
            let attempt = || async {
                let part = source
                    .download_shard_part(
                        shard_id,
                        sync_hash,
                        part_id,
                        handle.clone(),
                        cancel.clone(),
                    )
                    .await?;
                if matches!(
                    runtime_adapter.validate_state_part(
                        shard_id,
                        &state_root,
                        PartId { idx: part_id, total: num_state_parts },
                        &part,
                    ),
                    StatePartValidationResult::Valid
                ) {
                    let mut store_update = store.store_update();
                    let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id)).unwrap();
                    let bytes = part.to_bytes();
                    store_update.set(DBCol::StateParts, &key, &bytes);
                    store_update.commit();
                } else {
                    return Err(near_chain::Error::Other("Part data failed validation".to_owned()));
                }
                Ok(())
            };
```

**File:** chain/chain/src/spice/chunk_validation.rs (L49-57)
```rust
    let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
    if !shard_layout.shard_ids().contains(&shard_id) {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Shard layout for block's ({:?}) epoch ({:?}) doesn't contain witness shard {:?}",
            block.hash(),
            epoch_id,
            shard_id
        )));
    }
```

**File:** chain/chain/src/chain.rs (L2699-2730)
```rust
    pub fn set_state_finalize(
        &mut self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
    ) -> Result<(), Error> {
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
        chain_update.commit()?;

        // We restored the state on height `shard_state_header.chunk.header.height_included`.
        // Now we should build a chain up to height of `sync_hash` block.
        loop {
            height += 1;
            let mut chain_update = self.chain_update();
            // Result of successful execution of set_state_finalize_on_height is bool,
            // should we commit and continue or stop.
            if chain_update.set_state_finalize_on_height(height, shard_id, sync_hash)? {
                chain_update.commit()?;
            } else {
                break;
            }
        }

        let flat_storage_manager = self.runtime_adapter.get_flat_storage_manager();
        if let Some(flat_storage) = flat_storage_manager.get_flat_storage_for_shard(shard_uid) {
            let header = self.get_block_header(&sync_hash)?;
            flat_storage.update_flat_head(header.prev_hash()).unwrap();
        }

        Ok(())
```
