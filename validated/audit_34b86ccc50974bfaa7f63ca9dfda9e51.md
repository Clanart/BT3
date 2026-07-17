Let me analyze the external bug's core invariant: **when setting a cross-reference between two components, the back-reference from the target component must be validated to match the current component's identity**. I'll search for nearcore analogs involving shard/epoch binding, state sync, chunk reconstruction, and similar cross-reference patterns.

### Title
Missing shard_id binding check in `set_state_header` allows cross-shard state substitution — (File: `chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` from a peer and stores it under the key `StateHeaderKey(shard_id, sync_hash)`. It validates the chunk's internal proofs and its Merkle inclusion in the block, but **never verifies that the chunk embedded in the header actually belongs to the requested `shard_id`**. A malicious peer can supply a valid header for shard X while claiming it is for shard Y. All existing checks pass, and the header — carrying shard X's `prev_state_root` — is committed to the DB under shard Y's key. Every subsequent step (state-part validation, `set_state_finalize`, trie population) then operates on the wrong shard's state root, silently installing shard X's trie data into shard Y's storage.

---

### Finding Description

`set_state_header` in `chain/chain/src/state_sync/adapter.rs` performs five validation steps before persisting the header:

1. `validate_chunk_proofs` — verifies the chunk's internal encoded-merkle-root and encoded-length proofs.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk_hash, height_included))` — verifies the chunk hash appears somewhere in the block's chunk-headers Merkle tree.
3. `verify_path` for `prev_chunk_header` — same for the previous chunk.
4. Receipt-proof chain — verifies incoming receipts using `ReceiptList(shard_id, receipts)`.
5. `validate_state_root_node` — verifies the state-root node matches `chunk.prev_state_root()`.

**None of these steps check `chunk.cloned_header().shard_id() == shard_id`.**

The Merkle-inclusion check (step 2) is position-specific — the `MerklePath` encodes left/right directions that implicitly fix the leaf's position in the tree — but it only proves the chunk hash is present at *some* position in the block. It does not prove the chunk occupies the position that corresponds to the caller-supplied `shard_id`. A malicious peer can provide:

- A valid chunk for shard X (chunk_hash = H_X, shard_id = X, at tree position X).
- The correct Merkle proof for position X.
- An empty `incoming_receipts_proofs` / `root_proofs` (bypassing step 4 entirely).
- A valid `state_root_node` for shard X's `prev_state_root`.

All five checks pass. The header is stored under `StateHeaderKey(shard_Y, sync_hash)`.

The receipt-proof check (step 4) would catch the mismatch only when `incoming_receipts_proofs` is non-empty, because the hash is computed as `CryptoHash::hash_borsh(ReceiptList(shard_id, receipts))` using the caller's `shard_id`. When the list is empty — a common condition in epochs with no cross-shard traffic — the loop body never executes and the mismatch goes undetected.

After the poisoned header is stored:

- `set_state_part` retrieves the header for shard Y, extracts `chunk.prev_state_root()` (which is shard X's root), and validates incoming state parts against it. Parts for shard X pass; parts for shard Y would fail.
- `set_state_finalize` calls `apply_chunk` with `RuntimeStorageConfig::new(chunk_header.prev_state_root(), true)` and writes the result into `shard_uid` derived from the caller's `shard_id` (Y). Shard X's trie data is committed to shard Y's column.

---

### Impact Explanation

A syncing node that accepts the poisoned header will populate shard Y's trie with shard X's account/contract state. When the node subsequently attempts to apply live blocks, the block's chunk for shard Y carries the real `prev_state_root` for shard Y; the node's stored root is shard X's root. Every block application for shard Y fails with a state-root mismatch, permanently stalling the node until it wipes and re-syncs.

For a validator that is catching up to a new shard assignment, this causes it to miss its production window and be kicked out of the validator set, with direct economic loss.

---

### Likelihood Explanation

- **Attacker**: any peer on the network; no stake or special role required.
- **Trigger condition**: `incoming_receipts_proofs` is empty for the target shard in the sync epoch. This is routine — shards with no cross-shard receipts in the relevant block range satisfy it trivially.
- **Data required**: the attacker needs a valid chunk for any shard in the same block (publicly available from any full node) and the corresponding Merkle proof (also public). No cryptographic forgery is needed.
- **Detection**: none at the `set_state_header` layer; the error surfaces only after `set_state_finalize` when block application fails.

---

### Recommendation

Add an explicit shard-id binding check immediately after `validate_chunk_proofs`, before any further validation:

```rust
// Binding check: the chunk inside the header must belong to the requested shard.
if chunk.cloned_header().shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This mirrors the check already present in `validate_block_impl` (`chain/chain/src/chain.rs` line 800) and in `verify_chunk_shard_id` (`chain/chunks/src/shards_manager_actor.rs` line 1503), which both assert `chunk_header.shard_id() == expected_shard_id` before accepting chunk data.

---

### Proof of Concept

**Setup**: 4-shard network. Syncing node requests state for shard 1 (`shard_id = 1`). Malicious peer holds the block at `sync_hash`.

1. Malicious peer extracts the chunk for shard 0 from the block: `chunk_0` with `chunk_0.shard_id() = 0`, `chunk_0.prev_state_root() = R_0`.
2. Malicious peer computes the Merkle proof for shard 0's position in `chunk_headers_root`: `proof_0`.
3. Malicious peer constructs `ShardStateSyncResponseHeaderV2 { chunk: chunk_0, chunk_proof: proof_0, prev_chunk_header: ..., incoming_receipts_proofs: vec![], root_proofs: vec![], state_root_node: valid_node_for_R_0 }`.
4. Malicious peer sends this header in response to the syncing node's `StateRequestHeader { shard_id: 1, sync_hash }`.
5. Syncing node calls `set_state_header(shard_id=1, sync_hash, header)`.
   - `validate_chunk_proofs(chunk_0)` → passes (chunk_0 is internally valid).
   - `verify_path(chunk_headers_root, proof_0, ChunkHashHeight(chunk_0_hash, h))` → passes (chunk_0 IS in the tree at position 0).
   - `incoming_receipts_proofs` is empty → receipt loop skipped.
   - `validate_state_root_node(node_for_R_0, R_0)` → passes.
   - Header stored under `StateHeaderKey(shard_id=1, sync_hash)` with `prev_state_root = R_0`.
6. Syncing node downloads and validates state parts against `R_0` (shard 0's root). Parts for shard 0 pass.
7. `set_state_finalize(shard_id=1, sync_hash)` applies shard 0's trie data into shard 1's DB column.
8. Syncing node's shard 1 state is now shard 0's data. All subsequent block applications for shard 1 fail.

**Relevant code locations**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/primitives/src/state_sync.rs (L92-122)
```rust
pub struct ShardStateSyncResponseHeaderV2 {
    /// The chunk whose header in included as B_prev.chunks[shard_id]
    /// This chunk will be applied after downloading state
    pub chunk: ShardChunk,
    /// A merkle path for (Self::chunk.hash, Self::chunk.height_included), verifiable
    /// against B_prev.chunk_headers_root
    pub chunk_proof: MerklePath,
    /// This is None if sync_hash is the genesis hash. Otherwise, it's B_prev_chunk.chunks[shard_id]
    pub prev_chunk_header: Option<ShardChunkHeader>,
    /// A merkle path for (Self::prev_chunk_header.hash, Self::prev_chunk_header.height_included), verifiable
    /// against B_prev_chunk.chunk_headers_root
    pub prev_chunk_proof: Option<MerklePath>,
    /// This field contains the incoming receipts for shard_id for B_sync and B_prev_chunk.
    /// So, this field has at most two elements.
    /// These receipts are used to apply `chunk` after downloading state
    pub incoming_receipts_proofs: Vec<ReceiptProofResponse>,
    /// This field contains the info necessary to verify that the receipt proofs in Self::incoming_receipts_proofs
    /// are actually the ones referenced on chain
    ///
    /// The length of this field is the same as the length of Self::incoming_receipts_proofs, and elements
    /// of the two at a given index are taken together for verification. For a given index i,
    /// root_proofs[i] is a vector of the same length as incoming_receipts_proofs[i].1 , which itself is a
    /// vector of receipt proofs for all "from_shard_ids" that sent receipts to shard_id. root_proofs[i][j]
    /// contains a merkle root equal to the prev_outgoing_receipts_root field of the corresponding chunk
    /// included in the block with hash incoming_receipts_proofs[i].0, and a merkle path to verify it against
    /// that block's prev_chunk_outgoing_receipts_root field.
    pub root_proofs: Vec<Vec<RootProof>>,
    /// The state root with hash equal to B_prev.chunks[shard_id].prev_state_root.
    /// That is, the state root node of the trie before applying the chunks in B_prev
    pub state_root_node: StateRootNode,
}
```

**File:** chain/chain/src/chain.rs (L799-802)
```rust
            } else if chunk_header.is_new_chunk() {
                if chunk_header.shard_id() != shard_id {
                    return Err(Error::InvalidShardId(chunk_header.shard_id()));
                }
```
