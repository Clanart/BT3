### Title
Missing `from_shard_id`-to-root position binding in `set_state_header` receipt proof validation allows state reconstruction with incorrect receipts — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` validates incoming receipt proofs during state sync by checking (a) uniqueness of `from_shard_id`, (b) that `receipts_hash` is in the supplied `root`, and (c) that `root` is somewhere in the block's `prev_chunk_outgoing_receipts_root` merkle tree. It never verifies that `root` is at the leaf position corresponding to `from_shard_id`. A malicious peer can supply the same `root` (and its valid block-level merkle path) for every receipt proof entry, each with a distinct `from_shard_id` label, causing the syncing node to apply one shard's receipts N times and omit all other shards' receipts, producing an incorrect state root.

---

### Finding Description

In `set_state_header`, step 4 iterates over `incoming_receipts_proofs` and for each `ReceiptProof` performs: [1](#0-0) 

The critical lines are:

```rust
let ShardProof { from_shard_id, to_shard_id: _, proof } = shard_proof;
// 4d. uniqueness of from_shard_id only
visited_shard_ids.insert(*from_shard_id);

let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j]; // attacker-supplied
let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
// 4e. receipts_hash ∈ root
verify_path(*root, proof, &receipts_hash)
// 4f. root ∈ block's prev_chunk_outgoing_receipts_root
verify_path(*block_header.prev_chunk_outgoing_receipts_root(), block_proof, root)
```

Two structural gaps exist:

1. **`to_shard_id` is silently discarded** (`to_shard_id: _`). The hash is recomputed with the caller-supplied `shard_id`, so the implicit binding is correct for the receipts themselves — but the field is never cross-checked.

2. **`root` is not bound to `from_shard_id`'s shard index.** Check 4f uses `verify_path` (not `verify_path_with_index`), which verifies only that `root` is reachable from the block's merkle root via the supplied path — it does **not** verify the leaf position. The block's `prev_chunk_outgoing_receipts_root` is a merkle tree over all chunks' outgoing-receipt roots ordered by shard index. A valid merkle path for shard X's root at position X passes `verify_path` regardless of what `from_shard_id` claims.

Compare with the part-validation path, which correctly uses the position-aware variant: [2](#0-1) 

The `root_proofs` array is entirely attacker-controlled and has no uniqueness constraint on the `root` values themselves: [3](#0-2) 

The count check (4c) only enforces `receipt_proofs.len() == block_header.chunks_included()`: [4](#0-3) 

So a malicious peer can supply N proofs (one per included chunk), all pointing to `root_X` (shard X's outgoing-receipt root) with N distinct `from_shard_id` labels. Each passes 4d (unique labels), 4e (receipts from X to `shard_id` are genuinely in `root_X`), and 4f (`root_X` is genuinely in the block's merkle tree). The syncing node applies shard X's receipts N times and omits every other shard's receipts.

The resulting incorrect state is then committed: [5](#0-4) 

No post-finalization check compares the produced state root against the expected on-chain root before the node begins applying subsequent blocks.

---

### Impact Explanation

The syncing node reconstructs a state whose trie root diverges from the canonical chain. When it subsequently attempts to apply blocks, `validate_chunk_with_chunk_extra_and_receipts_root` will reject every chunk because `prev_state_root` will not match: [6](#0-5) 

The node is permanently stalled until it re-syncs from an honest peer. If the node is a validator, it cannot produce or endorse chunks, degrading network liveness. If all reachable peers are malicious, the node cannot recover without operator intervention.

**Severity: High** — broken state-reconstruction invariant, node permanently diverges from canonical chain.

---

### Likelihood Explanation

State sync headers are served by any reachable peer; no privileged role is required. The attack requires only:
- Knowledge of the target block's structure (public, since headers are already synced before state sync begins).
- Ability to compute valid merkle paths for `root_X` and for `receipts_X_to_shard_id` within `root_X` — both derivable from public block data.

A single malicious peer in the syncing node's peer set is sufficient.

---

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L463-469)
```rust
            // 4c. Checking len of receipt_proofs for current block
            if receipt_proofs.len() != shard_state_header.root_proofs()[i].len()
                || receipt_proofs.len() != block_header.chunks_included() as usize
            {
                byzantine_assert!(false);
                return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
            }
```

**File:** chain/chain/src/state_sync/adapter.rs (L476-503)
```rust
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
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1268-1278)
```rust
        if (part.part_ord as usize) < num_total_parts {
            if !verify_path_with_index(
                merkle_root,
                &part.merkle_proof,
                &part.part,
                part.part_ord,
                num_total_parts as u64,
            ) {
                return Err(Error::InvalidMerkleProof);
            }

```

**File:** core/primitives/src/state_sync.rs (L107-118)
```rust
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
```

**File:** chain/chain/src/chain_update.rs (L487-542)
```rust
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

**File:** chain/chain/src/validate.rs (L138-140)
```rust
    if *prev_chunk_extra.state_root() != chunk_header.prev_state_root() {
        return Err(Error::InvalidStateRoot);
    }
```
