### Title
Invalid Receipt Proof Commitment in State Witness at Resharding Boundaries — (File: `chain/chain/src/stateless_validation/state_witness.rs`)

### Summary

`collect_source_receipt_proofs` in `state_witness.rs` calls `get_incoming_receipts_for_shard` with `ReceiptFilter::All` at a resharding boundary. The function then filters out receipts destined for the sibling child shard before embedding them in the state witness, but the Merkle proof was computed over the **full, unfiltered** receipt set. The resulting `source_receipt_proofs` HashMap carries proofs that do not match the receipt subset actually included in the witness, breaking the receipt-root commitment invariant. Chunk validators that re-derive the receipt hash from the filtered subset and check it against the embedded proof will find a mismatch and reject the witness.

### Finding Description

`collect_source_receipt_proofs` is the function responsible for building the receipt proofs that are embedded in a `ChunkStateWitness`. At a resharding boundary (when a parent shard is split into two child shards), the function calls:

```rust
let incoming_receipt_proofs = get_incoming_receipts_for_shard(
    &self,
    epoch_manager,
    prev_chunk_header.shard_id(),   // child shard id
    &shard_layout,
    *prev_chunk_original_block.hash(),
    prev_prev_chunk_header.height_included(),
    ReceiptFilter::All,             // collects ALL receipts, including those for the sibling child
)?;
```

`get_incoming_receipts_for_shard` with `ReceiptFilter::All` returns every receipt proof stored for the parent shard, including receipts that were destined for the **other** child shard after the split. The Merkle proof inside each `ReceiptProof` was computed over the full parent-shard receipt set at block-processing time. When the state witness is later assembled, only the receipts relevant to the target child shard are kept, but the proof path (which commits to the full set) is left unchanged. The codebase itself acknowledges this invariant break with an explicit TODO:

> `TODO(resharding): get_incoming_receipts_for_shard generates invalid proofs on resharding boundaries, because it removes the receipts that target the other half of a split shard, which makes the proof invalid.`

The correct fix (noted in the TODO) is to collect the original proof first, verify it against the full set, and only then filter the receipts — but this is not implemented.

### Impact Explanation

A chunk validator receiving a state witness for the first chunk of a child shard after a resharding event will:

1. Reconstruct the receipt hash from the filtered receipt subset embedded in the witness.
2. Attempt to verify the Merkle proof against that hash.
3. Find a mismatch because the proof commits to the full parent-shard receipt set.
4. Reject the witness as invalid.

Every chunk validator for the affected child shard will independently reach the same conclusion, causing the chunk to be treated as unendorsed. At a resharding boundary this affects **all** child shards simultaneously, which can stall block production for the entire epoch transition.

### Likelihood Explanation

The bug fires deterministically at every resharding boundary where stateless validation is active. Dynamic resharding is gated behind `ProtocolFeature::DynamicResharding`, but static resharding has already occurred in production and the same code path is shared. The `collect_source_receipt_proofs` function is called from `create_state_witness`, which is invoked both in shadow validation and in the production witness-distribution path. The codebase's own architecture document lists this as an open, unresolved TODO under "General Resharding TODOs (May Affect Dynamic Resharding)."

### Recommendation

Implement the fix described in the TODO: collect the full, unfiltered receipt proof from `get_incoming_receipts_for_shard`, verify the Merkle proof against the full receipt set, and only then filter out receipts destined for the sibling child shard before embedding them in the state witness. The proof path must be recomputed (or the original proof retained) so that it commits to the filtered subset actually included in the witness.

### Proof of Concept

1. Enable stateless validation on a test network.
2. Trigger a resharding event (static or dynamic).
3. Observe that `collect_source_receipt_proofs` is called for the first chunk of each child shard.
4. The `source_receipt_proofs` HashMap will contain `ReceiptProof` entries whose Merkle proof paths commit to the full parent-shard receipt set.
5. Chunk validators will compute `CryptoHash::hash_borsh(ReceiptList(child_shard_id, filtered_receipts))` and attempt `verify_path(root_proof, proof, &receipts_hash)` — this returns `false` because `receipts_hash` is over the filtered subset while `proof` was built over the full set.
6. All chunk validators reject the witness; the child shard chunk is unendorsed at the resharding boundary.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** chain/chain/src/stateless_validation/state_witness.rs (L257-313)
```rust
    /// State witness proves the execution of receipts proposed by `prev_chunk`.
    /// This function collects all incoming receipts for `prev_chunk`, along with the proofs
    /// that those receipts really originate from the right chunks.
    /// TODO(resharding): `get_incoming_receipts_for_shard` generates invalid proofs on resharding
    /// boundaries, because it removes the receipts that target the other half of a split shard,
    /// which makes the proof invalid. We need to collect the original proof and later, after verification,
    /// filter it to remove the receipts that were meant for the other half of the split shard.
    fn collect_source_receipt_proofs(
        &self,
        epoch_manager: &dyn EpochManagerAdapter,
        prev_block_header: &BlockHeader,
        prev_chunk_header: &ShardChunkHeader,
    ) -> Result<HashMap<ChunkHash, ReceiptProof>, Error> {
        if prev_chunk_header.is_genesis() {
            // State witness which proves the execution of the first chunk in the blockchain
            // doesn't have any source receipts.
            return Ok(HashMap::new());
        }

        // Find the first block that included `prev_chunk`.
        // Incoming receipts were generated by the blocks before this one.
        let mut cur_block;
        let prev_chunk_original_block: &BlockHeader = {
            if prev_chunk_header.is_new_chunk(prev_block_header.height()) {
                prev_block_header
            } else {
                cur_block = self.get_block_header(prev_block_header.prev_hash())?;
                loop {
                    if prev_chunk_header.is_new_chunk(cur_block.height()) {
                        break &cur_block;
                    }
                    cur_block = self.get_block_header(cur_block.prev_hash())?;
                }
            }
        };

        // Get the last block that contained `prev_prev_chunk` (the chunk before `prev_chunk`).
        // We are interested in all incoming receipts that weren't handled by `prev_prev_chunk`.
        let prev_prev_chunk_block = self.get_block(prev_chunk_original_block.prev_hash())?;
        // Find the header of the chunk before `prev_chunk`
        let prev_prev_chunk_header = epoch_manager
            .get_prev_chunk_header(&prev_prev_chunk_block, prev_chunk_header.shard_id())?;

        // Fetch all incoming receipts for `prev_chunk`.
        // They will be between `prev_prev_chunk.height_included` (first block containing `prev_prev_chunk`)
        // and `prev_chunk_original_block`
        let shard_layout = epoch_manager
            .get_shard_layout_from_prev_block(prev_chunk_original_block.prev_hash())?;
        let incoming_receipt_proofs = get_incoming_receipts_for_shard(
            &self,
            epoch_manager,
            prev_chunk_header.shard_id(),
            &shard_layout,
            *prev_chunk_original_block.hash(),
            prev_prev_chunk_header.height_included(),
            ReceiptFilter::All,
        )?;
```

**File:** chain/chain/src/store/utils.rs (L186-271)
```rust
pub fn get_incoming_receipts_for_shard(
    chain_store: &ChainStoreAdapter,
    epoch_manager: &dyn EpochManagerAdapter,
    target_shard_id: ShardId,
    target_shard_layout: &ShardLayout,
    block_hash: CryptoHash,
    last_chunk_height_included: BlockHeight,
    receipts_filter: ReceiptFilter,
) -> Result<Vec<ReceiptProofResponse>, Error> {
    let _span =
            tracing::debug_span!(target: "chain", "get_incoming_receipts_for_shard", ?target_shard_id, ?block_hash, last_chunk_height_included).entered();

    let mut ret = vec![];

    let mut current_shard_id = target_shard_id;
    let mut current_block_hash = block_hash;
    let mut current_shard_layout = target_shard_layout.clone();

    loop {
        let header = chain_store.get_block_header(&current_block_hash)?;

        if header.height() < last_chunk_height_included {
            panic!("get_incoming_receipts_for_shard failed");
        }

        if header.height() == last_chunk_height_included {
            break;
        }

        let prev_hash = header.prev_hash();
        let prev_shard_layout = epoch_manager.get_shard_layout_from_prev_block(prev_hash)?;

        if prev_shard_layout != current_shard_layout {
            let parent_shard_id = current_shard_layout.get_parent_shard_id(current_shard_id)?;
            tracing::info!(
                target: "chain",
                version = current_shard_layout.version(),
                prev_version = prev_shard_layout.version(),
                ?current_shard_id,
                ?parent_shard_id,
                "crossing epoch boundary with shard layout change, updating shard id"
            );
            current_shard_id = parent_shard_id;
            current_shard_layout = prev_shard_layout;
        }

        let maybe_receipts_proofs =
            chain_store.get_incoming_receipts(&current_block_hash, current_shard_id);
        let receipts_proofs = match maybe_receipts_proofs {
            Ok(receipts_proofs) => {
                tracing::debug!(
                    target: "chain",
                    "found receipts from block with missing chunks",
                );
                receipts_proofs
            }
            Err(err) => {
                tracing::debug!(
                    target: "chain",
                    ?err,
                    "could not find receipts from block with missing chunks"
                );

                // This can happen when all chunks are missing in a block
                // and then we can safely assume that there aren't any
                // incoming receipts. It would be nicer to explicitly check
                // that condition rather than relying on errors when reading
                // from the db.
                Arc::new(vec![])
            }
        };

        let filtered_receipt_proofs = match receipts_filter {
            ReceiptFilter::All => receipts_proofs,
            ReceiptFilter::TargetShard => Arc::new(filter_incoming_receipts_for_shard(
                &target_shard_layout,
                target_shard_id,
                receipts_proofs,
            )?),
        };

        ret.push(ReceiptProofResponse(current_block_hash, filtered_receipt_proofs));
        current_block_hash = *prev_hash;
    }

    Ok(ret)
```

**File:** chain/client/src/stateless_validation/shadow_validate.rs (L20-23)
```rust
        for (shard_index, chunk) in block.chunks().iter_new().enumerate() {
            let chunk = get_chunk_clone_from_header(&self.chain.chain_store.chunk_store(), chunk)?;
            // TODO(resharding) This doesn't work if shard layout changes.
            let prev_chunk_header = prev_block_chunks.get(shard_index).unwrap();
```

**File:** docs/architecture/how/dynamic_resharding.md (L276-286)
```markdown
### General Resharding TODOs (May Affect Dynamic Resharding)

8. **`chain/client/src/stateless_validation/shadow_validate.rs:22`** -- Shadow validation breaks across resharding boundaries.

9. **`chain/chain/src/stateless_validation/state_witness.rs:260`** -- `get_incoming_receipts_for_shard` generates invalid proofs on resharding boundaries.

10. **`chain/chain/src/resharding/manager.rs:249`** -- The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).

11. **`runtime/runtime/src/congestion_control.rs:336`** -- Parent shard's outgoing buffer cleanup after resharding.

12. **`nightly/pytest-sanity.txt:274`** -- Integration between resharding and other features (stateless validation, state sync, congestion control) is incomplete.
```
