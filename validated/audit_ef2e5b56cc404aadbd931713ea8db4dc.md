### Title
Missing Shard-ID Binding Check in State Sync Header Acceptance Allows Cross-Shard State Corruption - (File: chain/chain/src/state_sync/adapter.rs)

---

### Summary

`set_state_header` in `chain/chain/src/state_sync/adapter.rs` accepts a `shard_id` parameter and a `ShardStateSyncResponseHeader` (which embeds a `ShardChunk` with its own `shard_id` field), but **never asserts that the chunk's `shard_id` equals the requested `shard_id`**. A malicious peer serving state sync data can supply a header whose embedded chunk belongs to a different shard. All existing checks pass, the header is stored under the wrong `StateHeaderKey(shard_id, sync_hash)`, and the subsequent `set_state_finalize` call applies the wrong shard's state root and transactions to the syncing node's storage for the requested shard, permanently corrupting it.

---

### Finding Description

`set_state_header` performs five categories of checks before committing to `DBCol::StateHeaders`:

1. `validate_chunk_proofs` — validates the chunk's internal body/header consistency.
2. `verify_path` against `sync_prev_block_header.chunk_headers_root()` — verifies the chunk is *somewhere* in the block's chunk merkle tree, but does **not** verify it occupies the slot for `shard_id`.
3. `prev_chunk` merkle path check — same issue; verifies inclusion, not shard position.
4. Receipt proof chain — verified against the caller-supplied `shard_id`, not the chunk's own `shard_id`.
5. `validate_state_root_node` — validates the state root node against `chunk_inner.prev_state_root()`, which is the *chunk's* state root (from the wrong shard).

None of these checks enforce the invariant `chunk.shard_id() == shard_id`. After passing all checks, the header is stored:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

The key uses the caller-supplied `shard_id`, but the value contains a chunk for a different shard. [1](#0-0) 

The `StateHeaderKey` type is `(ShardId, CryptoHash)` — the shard-id/sync-hash pair is the sole index into `DBCol::StateHeaders`. [2](#0-1) 

The `ShardStateSyncResponseHeaderV2` struct embeds a full `ShardChunk` (which carries its own `shard_id` in its header), but `set_state_header` never reads `chunk.cloned_header().shard_id()` for comparison. [3](#0-2) 

The `verify_path` call at step 2 only proves the chunk hash is *present* in the block's chunk merkle tree; it does not constrain which shard slot the chunk occupies. A chunk from shard 1 has a valid merkle proof for its own position (slot 1), and that proof passes the check even when `shard_id=0` was requested. [4](#0-3) 

After the corrupted header is stored, `set_state_finalize` retrieves it via `get_state_header(shard_id, sync_hash)` and calls `chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)`, which applies the embedded chunk's transactions and `prev_state_root` (belonging to the wrong shard) under the `shard_uid` derived from the requested `shard_id`. [5](#0-4) 

The `set_state_finalize` in `chain_update.rs` derives `shard_uid` from the caller-supplied `shard_id` and then calls `apply_chunk` with `chunk_header.prev_state_root()` — which is the wrong shard's state root — writing the result into the wrong shard's storage slot. [6](#0-5) 

---

### Impact Explanation

A syncing node that accepts a cross-shard header will apply the wrong shard's trie state and transactions to its local storage for the requested shard. The resulting `ChunkExtra` and trie root stored for `shard_id` will be inconsistent with the canonical chain, causing the node to produce invalid blocks or fail block validation for every subsequent block on that shard. The corruption is persistent (written to RocksDB) and survives restarts. All shards on the node are independently vulnerable.

**Impact: High**

---

### Likelihood Explanation

State sync is triggered whenever a node falls behind the chain head. The download source is any peer in the network (or external storage). The `StateSyncDownloader` downloads the header from a `StateSyncDownloadSource` and forwards it to `set_state_header` via `StateHeaderValidationRequest`. No privileged role is required to serve state sync data — any peer can respond to `StateRequestHeader` messages. The attack requires constructing a valid `ShardStateSyncResponseHeader` for a different shard (all fields are publicly derivable from on-chain data) and serving it in response to a state sync request.

**Likelihood: Medium** (requires a malicious peer in the network and a syncing node)

---

### Recommendation

Add an explicit shard-id binding check at the top of `set_state_header`, immediately after extracting the chunk:

```rust
let chunk = shard_state_header.cloned_chunk();
// Enforce the core invariant: the chunk inside the header must belong to the requested shard.
if chunk.cloned_header().shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} does not match requested shard_id {:?}",
        chunk.cloned_header().shard_id(),
        shard_id,
    )));
}
```

This mirrors the pattern already used in `get_state_response_part`, which explicitly checks `shard_ids.contains(&shard_id)` before proceeding. [7](#0-6) 

---

### Proof of Concept

1. Node A is syncing and requests the state header for `shard_id=0`, `sync_hash=H`.
2. Malicious peer B intercepts or races the response. B constructs a `ShardStateSyncResponseHeaderV2` where:
   - `chunk` is the valid chunk for `shard_id=1` at the block before `H` (publicly available on-chain).
   - `chunk_proof` is the valid merkle path for shard 1's slot in that block's `chunk_headers_root`.
   - `prev_chunk_header` / `prev_chunk_proof` are valid for shard 1's previous chunk.
   - `incoming_receipts_proofs` / `root_proofs` are constructed for shard 0 (the parameter used in the receipt hash check at line 488).
   - `state_root_node` is the valid state root node for shard 1's `prev_state_root`.
3. Node A calls `set_state_header(shard_id=0, sync_hash=H, header_from_B)`.
4. All five checks pass: chunk proofs are internally valid; the merkle path proves shard 1's chunk is in the block; receipt proofs are valid for shard 0; the state root node matches shard 1's `prev_state_root`.
5. The header is stored under `StateHeaderKey(0, H)` in `DBCol::StateHeaders`. [1](#0-0) 
6. Node A calls `set_state_finalize(0, H)`, retrieves the corrupted header, and applies shard 1's state root and transactions under shard 0's `shard_uid`. [8](#0-7) 
7. Node A's shard 0 state is now permanently corrupted with shard 1's trie, causing all subsequent block production and validation for shard 0 to fail.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L305-309)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_ids = self.epoch_manager.shard_ids(epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
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

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** core/primitives/src/state_sync.rs (L19-23)
```rust
#[derive(PartialEq, Eq, Clone, Debug, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct StateHeaderKey(pub ShardId, pub CryptoHash);

#[derive(PartialEq, Eq, Clone, Debug, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct StatePartKey(pub CryptoHash, pub ShardId, pub u64 /* PartId */);
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

**File:** chain/chain/src/chain.rs (L2699-2731)
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
    }
```

**File:** chain/chain/src/chain_update.rs (L513-542)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                gas_limit,
                last_validator_proposals: chunk_header.prev_validator_proposals(),
                is_new_chunk: true,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext {
                block_type: BlockType::Normal,
                height: chunk_header.height_included(),
                prev_block_hash: *chunk_header.prev_block_hash(),
                block_timestamp: block_header.raw_timestamp(),
                gas_price,
                random_seed: *block_header.random_value(),
                congestion_info: block.block_congestion_info(),
                bandwidth_requests: block.block_bandwidth_requests(),
            },
            &receipts,
            transactions,
        )?;
```
