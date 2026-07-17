### Title
Missing Shard-ID Binding Check in `set_state_header` Allows Cross-Shard State Substitution — (File: `chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` verifies that the supplied chunk is present in the block via a **position-agnostic** Merkle path check, but never verifies that the chunk's own `shard_id` field matches the `shard_id` parameter. A malicious peer can substitute a chunk from a different shard, causing the syncing node to store the wrong shard's state root and ultimately apply the wrong trie state.

---

### Finding Description

`set_state_header` accepts a caller-supplied `shard_id` and a `ShardStateSyncResponseHeader` that embeds a `ShardChunk`. Step 3a verifies inclusion in the block: [1](#0-0) 

The call is to `verify_path`, which is **position-agnostic**: it only proves that `ChunkHashHeight(chunk_hash, height_included)` is *some* leaf in the block's `chunk_headers_root` Merkle tree. [2](#0-1) 

The block's `chunk_headers_root` is a Merkle tree over **all shards' chunks**. A valid Merkle path for shard Y's chunk will verify against the same root, regardless of which shard position it occupies. After this check, the code proceeds to receipt-proof validation and state-root-node validation — but **no step ever asserts `chunk.shard_id() == shard_id`**.

The header is then persisted under the key `StateHeaderKey(shard_id, sync_hash)`: [3](#0-2) 

`set_state_part` later retrieves this header and validates state parts against the embedded state root: [4](#0-3) 

If the header contains shard Y's chunk, the state root is shard Y's. State parts matching shard Y's trie will pass `validate_state_part`. Finally, `set_state_finalize` applies the chunk using the **caller-supplied** `shard_id` (shard X) to derive `shard_uid`, not the chunk's own `shard_id`: [5](#0-4) 

This causes shard Y's trie state to be written into shard X's storage slot.

---

### Impact Explanation

**Critical.** A syncing node's shard X state is replaced with shard Y's state. The node will subsequently produce or validate blocks using the wrong state root for shard X, leading to invalid block production, failed endorsements, and potential slashing. The corruption is committed to persistent storage via `store_update.commit()`.

---

### Likelihood Explanation

Any network peer can respond to state-sync requests. The `StateResponseInfo` message is accepted from any peer before authentication of the chunk's shard binding. Crafting the attack requires only: (1) taking shard Y's chunk from the target block, (2) computing its valid Merkle path in `chunk_headers_root`, and (3) sending it as the header for shard X. No privileged role is required.

---

### Recommendation

After extracting the chunk from the header in `set_state_header`, add an explicit shard-ID binding check before any further validation:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD: verify the chunk belongs to the requested shard
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} does not match requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

This mirrors the pattern already used in `validate_receipt_proof`, which explicitly checks `from_shard_id` and `to_shard_id` before accepting a proof: [6](#0-5) 

---

### Proof of Concept

1. Syncing node N requests `StateResponseHeader` for `shard_id=0`, `sync_hash=H` from malicious peer P.
2. P retrieves the real block at `H`. It takes shard 1's chunk (`chunk_1`) and computes a valid Merkle path for `ChunkHashHeight(chunk_1.hash, chunk_1.height_included)` against `block[H-1].chunk_headers_root`.
3. P sends a crafted `ShardStateSyncResponseHeader` with `chunk=chunk_1`, `chunk_proof=<path for shard 1>`, and valid receipt proofs for shard 0 (taken from the real block).
4. N calls `set_state_header(shard_id=0, sync_hash=H, crafted_header)`.
   - `validate_chunk_proofs(&chunk_1, ...)` passes (internal chunk consistency is fine).
   - `verify_path(chunk_headers_root, path_for_shard_1, ChunkHashHeight(chunk_1.hash, ...))` passes — shard 1's chunk is indeed in the block.
   - Receipt proofs for shard 0 validate correctly against the block.
   - State root node for shard 1's state root validates.
   - **No check that `chunk_1.shard_id() == 0`.**
5. Header stored under `StateHeaderKey(shard_0, H)` with shard 1's `prev_state_root`.
6. P provides state parts from shard 1's trie. `set_state_part` validates them against shard 1's state root — they pass.
7. `set_state_finalize(shard_id=0, H)` applies shard 1's trie to shard 0's `shard_uid`. Node N now has shard 1's state in shard 0's slot. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** chain/chain/src/state_sync/adapter.rs (L525-560)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();

        Ok(())
    }

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

**File:** core/primitives/src/merkle.rs (L112-119)
```rust
/// Verify merkle path for given item and corresponding path.
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** chain/chain/src/chain_update.rs (L452-514)
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

        // Note that block headers are already synced and can be taken
        // from store on disk.
        let block_header = get_block_header_on_chain_by_height(
            &self.chain_store_update.chain_store(),
            &sync_hash,
            chunk.height_included(),
        )?;

        // Getting actual incoming receipts.
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
        let is_genesis = block_header.height() == self.chain_store_update.get_genesis_height();
        let prev_block_header = (!is_genesis)
            .then(|| self.chain_store_update.get_block_header(block_header.prev_hash()))
            .transpose()?;

        // Prev block header should be present during state sync, since headers have been synced at
        // this point, except for genesis.
        let gas_price = if let Some(prev_block_header) = &prev_block_header {
            prev_block_header.next_gas_price()
        } else {
            block_header.next_gas_price()
        };

        let chunk_header = chunk.cloned_header();
        let gas_limit = chunk_header.gas_limit();
        let block = self.chain_store_update.get_block(block_header.hash())?;
        let transactions = chunk.to_transactions().to_vec();
        let transaction_validity = if let Some(prev_block_header) = prev_block_header {
            self.chain_store_update
                .chain_store()
                .compute_transaction_validity(&prev_block_header, &chunk)
        } else {
            vec![true; transactions.len()]
        };
        let transactions = SignedValidPeriodTransactions::new(transactions, transaction_validity);
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L537-554)
```rust
    if receipt_proof.1.from_shard_id != from_chunk.shard_id() {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Receipt proof for chunk {:?} is from shard {}, expected shard {}",
            from_chunk.chunk_hash(),
            receipt_proof.1.from_shard_id,
            from_chunk.shard_id(),
        )));
    }
    // Validate that to_shard_id is correct. to_shard_id is also encoded in the merkle tree,
    // so it's impossible to fake it.
    if receipt_proof.1.to_shard_id != target_chunk_shard_id {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Receipt proof for chunk {:?} is for shard {}, expected shard {}",
            from_chunk.chunk_hash(),
            receipt_proof.1.to_shard_id,
            target_chunk_shard_id
        )));
    }
```
