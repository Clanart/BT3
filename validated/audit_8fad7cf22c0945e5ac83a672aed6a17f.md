### Title
Missing Shard-ID Binding Validation in `set_state_header` Allows Cross-Shard State Corruption — (`File: chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header()` accepts a caller-supplied `shard_id` and a peer-supplied `ShardStateSyncResponseHeader`, but never verifies that the chunk embedded in the header actually belongs to `shard_id`. A malicious state-sync peer can supply a header whose embedded chunk belongs to a different shard (e.g., shard 1 while claiming shard 0), pass all existing merkle-path and hash checks, and cause the syncing node to store a cross-shard header under `StateHeaderKey(shard_id=0, sync_hash)`. Every subsequent step — `set_state_part`, `apply_state_part`, `set_state_finalize` — then operates on the wrong shard's state root, permanently corrupting the node's trie for the target shard.

### Finding Description

`set_state_header` performs five checks on the incoming header:

1. `validate_chunk_proofs` — verifies the chunk's internal hash consistency.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk_hash, height_included))` — verifies the chunk is a leaf somewhere in the block's chunk-headers Merkle tree.
3. `verify_path` on `prev_chunk_header`.
4. Receipt-proof chain validation.
5. `validate_state_root_node` — verifies the state-root node matches `chunk_inner.prev_state_root()`.

None of these checks compare `chunk.shard_id()` against the `shard_id` parameter.

The block's `chunk_headers_root` is a Merkle tree over **all** shard chunks ordered by shard index. `verify_path` reconstructs the root from the leaf and the path; it does not verify which position (shard index) the leaf occupies. A chunk from shard 1 has a perfectly valid Merkle path in the same tree, and that path will verify against `chunk_headers_root` regardless of which `shard_id` was requested.

An attacker therefore:
1. Obtains shard 1's `ShardChunk` and its Merkle path from the block at `sync_hash`.
2. Constructs a `ShardStateSyncResponseHeaderV2` embedding shard 1's chunk, shard 1's `state_root_node`, and shard 1's receipt proofs.
3. Sends this as the response to a state-header request for `shard_id = 0`.

All five checks pass. The header is committed to `DBCol::StateHeaders` under key `StateHeaderKey(shard_id=0, sync_hash)`. [1](#0-0) 

Downstream, `set_state_part` reads the stored header to obtain the state root:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
``` [2](#0-1) 

This is now shard 1's state root. Parts validated against it are shard 1's parts. `apply_state_part` then writes those trie nodes into shard 0's `ShardUId` slot:

```rust
let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
tries.apply_all(&trie_changes, shard_uid, &mut store_update);
``` [3](#0-2) 

Shard 0's trie storage now contains shard 1's trie nodes anchored at shard 1's state root. `set_state_finalize` then re-applies shard 1's chunk as if it were shard 0's, completing the corruption. [4](#0-3) 

### Impact Explanation

The syncing node's persistent state for shard 0 is replaced with shard 1's state. After `set_state_finalize` commits, the node believes it has a valid shard-0 state but is actually operating on shard-1 data. Consequences:

- The node produces chunks for shard 0 whose `prev_state_root` is shard 1's root; these chunks are rejected by honest validators.
- The node fails to validate legitimate shard-0 chunks from honest producers.
- The node cannot recover without a full re-sync; the corruption is written to the persistent store.

### Likelihood Explanation

State sync is performed by any node that falls behind or needs to catch up on a new shard (including validators during shard reshuffling). The syncing node requests headers from peers selected from the network. Any network participant — including non-validators — can serve state sync responses. No special privilege is required: the attacker only needs to be reachable as a peer when the victim requests a state header.

### Recommendation

After extracting the chunk from the header, assert that its `shard_id` matches the requested `shard_id` before proceeding:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD: binding check
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} does not match requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

This check should be placed immediately after `cloned_chunk()` is called, before any further validation, so that all subsequent checks operate on a chunk that is guaranteed to belong to the requested shard. [5](#0-4) 

### Proof of Concept

1. Syncing node N requests `StateRequestHeader { shard_id: 0, sync_hash }` from peer P.
2. P is malicious. P fetches the block at `sync_hash`, extracts shard 1's `ShardChunk` and its Merkle path in `chunk_headers_root`.
3. P constructs `ShardStateSyncResponseHeaderV2 { chunk: shard1_chunk, chunk_proof: shard1_merkle_path, state_root_node: shard1_state_root_node, ... }` and returns it.
4. N calls `set_state_header(shard_id=0, sync_hash, crafted_header)`.
   - `validate_chunk_proofs` passes: shard 1's chunk hash is internally consistent.
   - `verify_path(chunk_headers_root, shard1_merkle_path, ChunkHashHeight(shard1_hash, shard1_height))` passes: shard 1's chunk is a valid leaf in the tree.
   - `validate_state_root_node` passes: the node matches `shard1_chunk.prev_state_root()`.
   - No check on `chunk.shard_id() == 0`.
5. `DBCol::StateHeaders[StateHeaderKey(0, sync_hash)]` now stores shard 1's header.
6. N downloads and validates state parts against shard 1's state root (they are valid shard-1 parts).
7. `apply_state_part(shard_id=0, shard1_state_root, ...)` writes shard 1's trie into shard 0's `ShardUId`.
8. `set_state_finalize(shard_id=0, sync_hash)` applies shard 1's chunk as shard 0's chunk.
9. N's shard-0 state is permanently replaced with shard-1 data. [6](#0-5)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-531)
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-553)
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
```

**File:** chain/chain/src/runtime/mod.rs (L1516-1527)
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
        self.precompile_contracts(epoch_id, contract_codes)?;
        store_update.commit();
```

**File:** chain/chain/src/chain_update.rs (L452-468)
```rust
    pub fn set_state_finalize(
        &mut self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        shard_state_header: ShardStateSyncResponseHeader,
    ) -> Result<ShardUId, Error> {
        let _span =
            tracing::debug_span!(target: "sync", "chain_update_set_state_finalize", %shard_id, ?sync_hash).entered();
        let (chunk, incoming_receipts_proofs) = match shard_state_header {
            ShardStateSyncResponseHeader::V1(shard_state_header) => (
                ShardChunk::V1(shard_state_header.chunk),
                shard_state_header.incoming_receipts_proofs,
            ),
            ShardStateSyncResponseHeader::V2(shard_state_header) => {
                (shard_state_header.chunk, shard_state_header.incoming_receipts_proofs)
            }
        };
```
