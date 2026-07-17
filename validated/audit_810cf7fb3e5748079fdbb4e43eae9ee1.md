### Title
Missing `chunk.shard_id() == shard_id` binding check in state-sync header acceptance corrupts syncing node's shard state — (`File: chain/chain/src/state_sync/adapter.rs`)

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` from a peer and stores it under the caller-supplied `shard_id` key without ever verifying that the chunk embedded in the header actually belongs to that shard. A malicious peer can respond to a header request for shard Y with a cryptographically valid header for shard X. All existing checks pass, and the syncing node permanently installs shard X's state root and trie data into shard Y's storage, corrupting its local state.

### Finding Description

`set_state_header(shard_id, sync_hash, shard_state_header)` performs five checks before persisting the header:

1. `validate_chunk_proofs` — verifies the chunk's internal encoding/signature.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` — proves the chunk hash appears somewhere in the block's Merkle tree of all chunks.
3. `verify_path` for `prev_chunk_header`.
4. Incoming-receipts proof chain.
5. `validate_state_root_node` — checks the state root node against the chunk's `prev_state_root`.

**None of these checks bind the chunk to the requested `shard_id`.** The Merkle tree at `chunk_headers_root` covers all shards; a valid path for shard X's chunk proves only that shard X's chunk is *somewhere* in the block, not that it occupies the slot for shard Y. The chunk's own `shard_id()` field is never compared to the `shard_id` parameter.

After passing all checks, the header is stored under `StateHeaderKey(shard_id, sync_hash)` — using the caller-supplied `shard_id`, not the chunk's actual shard:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

Downstream, `set_state_part` retrieves this header, extracts `state_root = chunk.prev_state_root()` (shard X's root), and validates incoming parts against it. `apply_state_part` then calls `get_shard_uid_from_epoch_id(shard_id, epoch_id)` — resolving shard Y's `ShardUId` — and writes shard X's trie changes into shard Y's storage:

```rust
let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
tries.apply_all(&trie_changes, shard_uid, &mut store_update);
flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
```

The exact corrupted value is: **shard Y's trie and flat-storage entries are overwritten with shard X's trie nodes**, and shard Y's committed state root in the node's DB points to shard X's `prev_state_root`.

### Impact Explanation

**High.** A syncing node (validator or RPC) that accepts a cross-shard header installs the wrong state for the target shard. A validator node will subsequently produce chunks with an incorrect `prev_state_root`, which other validators will reject, causing the node to be unable to participate in consensus and potentially be slashed. An RPC node will serve incorrect state queries. The corruption is written to persistent storage and survives restarts.

### Likelihood Explanation

**Medium.** Any peer that has state-sync data can serve a cross-shard header. No privileged role is required. The attack requires the malicious peer to be selected as the state-sync source for the victim, which is probabilistic but achievable by a peer that is well-connected or that eclipses the syncing node's peer set. The attack is most impactful during epoch transitions when many nodes perform state sync simultaneously.

### Recommendation

Add an explicit shard-binding check at the top of `set_state_header`, immediately after extracting the chunk:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This mirrors the fix applied in the external report (`route.destination != block.chainid` → revert) and closes the binding gap before any further validation or storage.

### Proof of Concept

1. Syncing node N requests the state-sync header for `shard_id=1`, `sync_hash=H`.
2. Malicious peer P holds a valid `ShardStateSyncResponseHeader` for `shard_id=0` at the same `sync_hash=H` (both shards' chunks appear in the same block).
3. P responds with the shard-0 header.
4. N calls `set_state_header(shard_id=1, sync_hash=H, header_for_shard_0)`.
5. `validate_chunk_proofs` passes — the chunk's internal proofs are valid.
6. `verify_path(chunk_headers_root, proof, ChunkHashHeight(shard0_chunk_hash, h))` passes — shard 0's chunk IS in the block's Merkle tree.
7. All remaining checks pass against shard 0's data.
8. The header is stored under `StateHeaderKey(shard_id=1, H)` but contains shard 0's `prev_state_root`.
9. N downloads and validates state parts against shard 0's state root (they pass).
10. `apply_state_part(shard_id=1, state_root_of_shard_0, ...)` writes shard 0's trie nodes into shard 1's `ShardUId` column family and flat storage.
11. N's shard 1 state is now shard 0's state; N produces invalid chunks for shard 1 and is ejected from consensus. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/primitives/src/state_sync.rs (L91-122)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
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
