### Title
State Sync Header Accepts Caller-Supplied `shard_id` Without Binding It to the Chunk's Actual Shard — (`File: chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a caller-supplied `shard_id` and uses it as the storage key for the validated header, but never checks that `shard_id` matches the `shard_id` embedded in the chunk inside the `ShardStateSyncResponseHeader`. A malicious peer can supply a valid header for shard X while the syncing node stores it under shard Y's key, causing subsequent state parts to be validated against shard X's state root and applied into shard Y's trie namespace — a direct cross-shard state corruption.

### Finding Description

In `set_state_header`, the function receives a `shard_id: ShardId` parameter and a `shard_state_header: ShardStateSyncResponseHeader`. It extracts the chunk from the header and runs several validity checks:

1. `validate_chunk_proofs` — verifies internal Merkle consistency of the chunk body.
2. `verify_path(sync_prev_block_header.chunk_headers_root(), chunk_proof, ChunkHashHeight(...))` — verifies the chunk is included *somewhere* in the block's chunk headers Merkle tree.
3. Receipt proof validations.
4. `validate_state_root_node` — verifies the state root node matches the chunk's `prev_state_root`.

None of these checks verify that `chunk.shard_id() == shard_id`. The Merkle proof in step 2 proves the chunk is at *some* position in the block's chunk list, but the code never asserts that position corresponds to the requested `shard_id`. After all checks pass, the header is stored under `StateHeaderKey(shard_id, sync_hash)` — keyed by the *caller-supplied* shard ID, not the chunk's actual shard ID. [1](#0-0) 

The storage write at the end uses the unvalidated `shard_id`: [2](#0-1) 

`set_state_part` then retrieves the header by `(shard_id, sync_hash)`, extracts the state root from the stored (potentially wrong-shard) chunk, and validates incoming parts against it: [3](#0-2) 

`apply_state_part` then applies those trie changes into the `shard_uid` derived from the *requested* `shard_id` (Y), not the chunk's actual shard: [4](#0-3) 

### Impact Explanation

A malicious peer can:

1. Respond to a `StateRequestHeader { shard_id: Y, sync_hash }` with a `ShardStateSyncResponseHeader` containing shard X's chunk and a valid Merkle proof for shard X's position in the block.
2. The syncing node calls `set_state_header(shard_id=Y, ..., header_for_shard_X)`. All checks pass — the chunk is internally valid and is genuinely included in the block.
3. The header is stored under `StateHeaderKey(Y, sync_hash)`, binding shard Y's sync to shard X's state root.
4. The malicious peer then serves shard X's state parts in response to part requests for shard Y. These parts validate correctly against shard X's state root.
5. `apply_state_part` writes shard X's trie nodes into shard Y's `ShardUId` namespace.
6. `set_state_finalize` applies shard X's chunk transactions in shard Y's context.

The syncing node ends up with shard X's state installed as shard Y's state. If the node is a validator, it will compute wrong state roots for shard Y, produce invalid chunks, and be unable to participate in consensus for that shard.

### Likelihood Explanation

Any peer in the NEAR P2P network can respond to state sync requests. The syncing node selects peers from the network without requiring them to be trusted validators. The attack requires only that a malicious peer be reachable by the syncing node — a realistic condition for any node joining the network during an epoch transition. The malicious peer needs to serve a valid header for a real shard (trivially obtained by syncing normally) and valid parts for that shard (also trivially obtained).

### Recommendation

Add an explicit shard ID binding check immediately after extracting the chunk from the header in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// Bind: the chunk inside the header must belong to the requested shard.
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id doesn't match requested shard_id".into(),
    ));
}
```

This mirrors the existing pattern used in `compute_state_response_header`, which validates `shard_id` against the epoch's shard list before proceeding: [5](#0-4) 

### Proof of Concept

```
Precondition: network has shards [0, 1] in the current epoch.
Shard 0 has a large trie (e.g., 10 GB). Shard 1 has a small trie.

1. Syncing node N starts state sync for shard 1 (shard_id=1).
2. Malicious peer M intercepts the StateRequestHeader for shard 1.
3. M responds with ShardStateSyncResponseHeader containing:
   - chunk = shard 0's chunk (valid, signed by shard 0's producer)
   - chunk_proof = valid Merkle proof for shard 0's position in the block
   - state_root_node = shard 0's state root node
4. N calls set_state_header(shard_id=1, sync_hash, header_from_M).
   - validate_chunk_proofs(shard_0_chunk) → passes (chunk is internally valid)
   - verify_path(chunk_headers_root, shard_0_proof, shard_0_chunk_hash) → passes
   - validate_state_root_node(shard_0_state_root_node, shard_0_prev_state_root) → passes
   - Stored: StateHeaderKey(shard_id=1, sync_hash) → header_with_shard_0_state_root
5. N requests state parts for shard 1. M serves shard 0's parts.
6. set_state_part validates parts against shard 0's state root → passes.
7. apply_state_part writes shard 0's trie into ShardUId for shard 1.
8. set_state_finalize applies shard 0's chunk in shard 1's context.
Result: N has shard 0's state installed as shard 1's state.
        N computes wrong state roots for shard 1 on every subsequent block.
``` [6](#0-5) [4](#0-3)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L82-85)
```rust
        let shard_ids = self.epoch_manager.shard_ids(sync_block_epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L368-532)
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

        let block_header = get_block_header_on_chain_by_height(
            &self.chain_store,
            &sync_hash,
            chunk.height_included(),
        )?;
        // 3b. Checking that chunk `prev_chunk` is included into block at height before chunk.height_included
        // 3ba. Also checking prev_chunk.height_included - it's important for getting correct incoming receipts
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
        };

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

        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();

        Ok(())
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

**File:** chain/chain/src/runtime/mod.rs (L1501-1527)
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
```
