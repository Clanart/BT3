### Title
Missing Shard-ID Binding in `set_state_header` Allows Cross-Shard State Substitution — (File: `chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` validates that a peer-supplied chunk is included in the block via a merkle proof, but never checks that the chunk's `shard_id` matches the requested `shard_id`. A malicious peer can supply a chunk from shard X (with a valid merkle proof for shard X's position) when the syncing node requests state for shard Y. The proof passes `verify_path` because the chunk IS in the block at position X; the wrong-shard chunk is then stored as the state header for shard Y, causing all subsequent state parts to be validated against shard X's state root and applied to shard Y's trie.

---

### Finding Description

In `set_state_header`, step 3a verifies the chunk is in the block: [1](#0-0) 

```rust
if !verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
) {
```

`verify_path` is defined as: [2](#0-1) 

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

It only checks `compute_root_from_path(path, hash(leaf)) == root`. It does **not** check the position. The position-aware variant `verify_path_with_index` (which calls `verify_path_matches_index`) exists but is not used here: [3](#0-2) 

A chunk from shard 0 at position 0 in the block has a valid merkle proof that produces the correct `chunk_headers_root`. That proof passes `verify_path` even when the syncing node is requesting state for shard 1. There is no subsequent check that `chunk.shard_id() == shard_id` anywhere in `set_state_header`. [4](#0-3) 

After the header passes all five validation steps, it is stored in `DBCol::StateHeaders` keyed by `StateHeaderKey(shard_id, sync_hash)`. The state root used for all subsequent part validation in `set_state_part` is extracted from this stored header: [5](#0-4) 

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// validate_state_part uses this state_root — which is shard X's root, not shard Y's
```

Parts validated against shard X's state root are then applied to shard Y's trie in `apply_state_part`: [6](#0-5) 

The `state_root_node` validation in step 5 only confirms that the node's `data` hashes to `chunk_inner.prev_state_root()` and that `memory_usage` is consistent with `data`: [7](#0-6) 

This is fully satisfiable by a legitimate shard X node — it does not bind the node to shard Y.

---

### Impact Explanation

A malicious peer causes a syncing node to reconstruct shard Y's state entirely from shard X's trie data. The node ends up with a completely wrong state for shard Y. If the node is a validator, it will produce incorrect `prev_state_root` values in chunk headers, leading to slashing. Non-validator nodes will serve incorrect query results and be unable to participate correctly in consensus after sync completes. The corruption is silent — no error is raised during or after `set_state_finalize`. [8](#0-7) 

**Severity: High**

---

### Likelihood Explanation

Any peer on the network can execute this attack. The attacker only needs:
1. The block's `chunk_headers_root` (public, in every block header)
2. The merkle proof for a chunk from a different shard (public, derivable from the block)
3. A valid `state_root_node` for the substitute chunk's state root (public, readable from any full node)

No privileged role (validator, chunk producer, storage operator) is required. The attack is triggered during state sync, which occurs whenever a node bootstraps or falls behind. The syncing node accepts state responses from any peer.

**Likelihood: Medium** (requires a malicious peer, but no special privilege)

---

### Recommendation

Add a shard-ID binding check immediately after the chunk is extracted in `set_state_header`, before any other validation:

```rust
// After: let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
``` [9](#0-8) 

---

### Proof of Concept

**Setup**: Network has 2 shards. Syncing node requests state header for `shard_id = 1`, `sync_hash = H`.

**Block at `prev(H)`** has chunks:
- Position 0: `chunk0` (shard 0, `prev_state_root = state_root_0`)
- Position 1: `chunk1` (shard 1, `prev_state_root = state_root_1`)

`chunk_headers_root = combine_hash(hash(ChunkHashHeight(chunk0.hash, h0)), hash(ChunkHashHeight(chunk1.hash, h1)))`

**Attack**:

1. Malicious peer constructs a `ShardStateSyncResponseHeader` with:
   - `chunk = chunk0` (shard 0's chunk, `prev_state_root = state_root_0`)
   - `chunk_proof = [Right: hash(ChunkHashHeight(chunk1.hash, h1))]` — valid proof for position 0
   - `state_root_node` = legitimate node for `state_root_0` (fetched from any full node)

2. `set_state_header(shard_id=1, sync_hash=H, header)` is called:
   - `validate_chunk_proofs(chunk0)` → **passes** (chunk0 is internally valid)
   - `verify_path(chunk_headers_root, proof, ChunkHashHeight(chunk0.hash, h0))` → **passes** (proof is valid for position 0; `verify_path` does not check position)
   - **No check** that `chunk0.shard_id() == 1`
   - `validate_state_root_node(state_root_node, state_root_0)` → **passes**
   - Header stored as `StateHeaderKey(shard_id=1, sync_hash=H)` with `prev_state_root = state_root_0`

3. Syncing node downloads parts for shard 0's state, validates them against `state_root_0` (passes), and applies them to shard 1's trie via `apply_state_part(shard_id=1, state_root=state_root_0, ...)`.

4. Shard 1's trie now contains shard 0's state. The node has silently incorrect state for shard 1. [10](#0-9)

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

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** core/primitives/src/merkle.rs (L121-129)
```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash,
    path: &MerklePath,
    item: T,
    part_idx: u64,
    num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
}
```

**File:** chain/chain/src/runtime/mod.rs (L1501-1528)
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
        Ok(())
```

**File:** chain/chain/src/runtime/mod.rs (L1546-1571)
```rust
    fn validate_state_root_node(
        &self,
        state_root_node: &StateRootNode,
        state_root: &StateRoot,
    ) -> StateRootNodeValidationResult {
        if state_root == &Trie::EMPTY_ROOT {
            return if state_root_node == &StateRootNode::empty() {
                StateRootNodeValidationResult::Valid
            } else {
                StateRootNodeValidationResult::Invalid
            };
        }
        if hash(&state_root_node.data) != *state_root {
            return StateRootNodeValidationResult::Invalid;
        }
        match Trie::get_memory_usage_from_serialized(&state_root_node.data) {
            Ok(memory_usage) => {
                if memory_usage == state_root_node.memory_usage {
                    StateRootNodeValidationResult::Valid
                } else {
                    StateRootNodeValidationResult::Invalid
                }
            }
            Err(_) => StateRootNodeValidationResult::Invalid, // Invalid state_root_node
        }
    }
```

**File:** chain/chain/src/chain_update.rs (L452-548)
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

        self.chain_store_update.save_chunk(chunk);

        // `save_chunk` and `save_incoming_receipt` are chain-only and stay
        // inline; everything else goes through the shared helper.
        let config = self.chain_store_update.chain_store().chunk_persistence_config();
```
