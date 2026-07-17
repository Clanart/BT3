The code is clear enough to make a determination. Let me confirm the exact behavior of `validate_chunk_proofs` and the Merkle proof check.

`validate_chunk_proofs` (lines 22-67 of `chain/chain/src/validate.rs`) only checks:
- Chunk header hash correctness
- Transaction root correctness
- Outgoing receipts root correctness

It does **not** check `chunk.shard_id()` against any external `shard_id` argument.

The `verify_path` call at line 394-403 of `adapter.rs` verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a leaf in the Merkle tree rooted at `chunk_headers_root`. This proves the chunk is *somewhere* in the block — not that it is at the position corresponding to the requested `shard_id`.

---

### Title
Missing `chunk.shard_id() == shard_id` guard in `set_state_header` allows cross-shard state root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded chunk belongs to a different shard than the `shard_id` argument, stores it under `StateHeaderKey(shard_id, sync_hash)`, and causes `set_state_part` to validate and install state parts against the wrong shard's `prev_state_root`.

### Finding Description

`set_state_header` performs five validation steps before persisting the header:

1. `validate_chunk_proofs` — verifies internal chunk hash/body consistency, no shard_id check. [1](#0-0) 

2. `verify_path` against `chunk_headers_root` — proves the chunk hash is *a* leaf in the block's Merkle tree, but does **not** verify the leaf's position corresponds to `shard_id`. [2](#0-1) 

3. `prev_chunk` Merkle proof — same position-agnostic membership check. [3](#0-2) 

4. Receipt proof validation — hashes receipts with the *argument* `shard_id`, not `chunk.shard_id()`, so valid receipt proofs for the requested shard S1 pass even when the embedded chunk belongs to S2. [4](#0-3) 

5. `validate_state_root_node` — validates the state root node against `chunk_inner.prev_state_root()` (S2's root), which the attacker can supply correctly. [5](#0-4) 

After all five checks pass, the header is stored under `StateHeaderKey(shard_id=S1, sync_hash)` — keyed by the *argument*, not the chunk's actual shard: [6](#0-5) 

There is no assertion of the form `chunk.shard_id() == shard_id` anywhere in this function. [7](#0-6) 

`set_state_part` then reads the stored header, extracts `prev_state_root` from the embedded chunk (S2's root), and validates incoming parts against it under `shard_id=S1`: [8](#0-7) 

`set_state_finalize` subsequently applies the chunk and saves a `ChunkExtra` for `shard_uid` derived from the argument `shard_id`, but using `chunk_header.prev_state_root()` — which is S2's root: [9](#0-8) 

### Impact Explanation

A syncing node that accepts a crafted `ShardStateSyncResponseHeader` will:
- Store S2's `prev_state_root` under S1's `StateHeaderKey`
- Validate and accept state parts that are valid for S2's trie
- Finalize state sync for S1 using S2's trie root, installing S2's account/contract state under S1's `ShardUId`

This corrupts the node's local state for shard S1 permanently. The node will subsequently produce or validate chunks for S1 against the wrong state root, causing it to diverge from the canonical chain.

### Likelihood Explanation

Any peer on the NEAR P2P network can respond to `StateRequestHeader` messages. No validator or operator privilege is required. The attacker needs:
- A valid block with chunks for both S1 and S2 (always true in a multi-shard network)
- Knowledge of S1's actual incoming receipts for the relevant block range (available from any full node)
- S2's state to serve valid state parts afterward

All three are available to an unprivileged full node operator.

### Recommendation

Add an explicit shard identity check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD THIS:
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
``` [10](#0-9) 

### Proof of Concept

1. Set up a two-shard network (S0, S1) and identify a `sync_hash`.
2. Obtain the real `ShardStateSyncResponseHeader` for S1 (chunk for S1, receipt proofs for S1).
3. Replace the `chunk` field with the S0 chunk from the same block, keeping the S1 receipt proofs and constructing a valid Merkle proof for the S0 chunk against `chunk_headers_root`.
4. Call `set_state_header(shard_id=S1, sync_hash, crafted_header)`.
5. Assert: the call returns `Ok(())` and `DBCol::StateHeaders` contains an entry under `StateHeaderKey(S1, sync_hash)` whose embedded chunk has `shard_id() == S0`.
6. Call `set_state_part(shard_id=S1, ...)` with parts valid for S0's `prev_state_root`; assert they are accepted.
7. Call `set_state_finalize(shard_id=S1, ...)` and assert the resulting `ChunkExtra` for S1's `ShardUId` contains S0's state root.

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

**File:** chain/chain/src/chain_update.rs (L513-520)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
```
