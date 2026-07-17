Based on my thorough code analysis, here is the finding:

---

### Title
Missing shard_id binding in `set_state_header` allows cross-shard state root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` never checks that `chunk.shard_id() == shard_id`. The `verify_path` call that proves the chunk is in the block's `chunk_headers_root` is position-blind: it only checks that the chunk hash is reachable from the root via the supplied path, not that the path corresponds to the shard index for `shard_id`. An unprivileged peer can supply a `ShardStateSyncResponseHeader` whose embedded chunk belongs to shard 1 while the call is made with `shard_id=0`, pass every validation gate, and cause the node to store shard 1's `prev_state_root` under `StateHeaderKey(0, sync_hash)`.

### Finding Description

**Entry point.** A syncing node sends `StateRequestHeader { shard_id: 0, sync_hash }` over the network. Any peer — no special privilege required — can respond with an arbitrary `ShardStateSyncResponseHeader`. The node calls:

```
set_state_header(shard_id=0, sync_hash, attacker_header)
```

**Validation gates in `set_state_header`** (`chain/chain/src/state_sync/adapter.rs` lines 368–531):

| Step | Check | Blocks cross-shard substitution? |
|------|-------|----------------------------------|
| 1–2 | `validate_chunk_proofs` | No — checks internal hash/tx/receipt consistency only, no shard_id |
| 3a | `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk.chunk_hash(), ...))` | **No** — position-blind |
| 3b | `verify_path(prev_chunk_headers_root, prev_chunk_proof, ...)` | **No** — same issue |
| 4e | `verify_path(root, proof, ReceiptList(shard_id, receipts))` | No — uses parameter `shard_id=0`, attacker supplies matching receipts |
| 4f | `verify_path(prev_chunk_outgoing_receipts_root, block_proof, root)` | No — public data |
| 5 | `validate_state_root_node(state_root_node, chunk_inner.prev_state_root())` | No — validates node against chunk's own root, which is shard 1's |

**The position-blind `verify_path` problem.** The `chunk_headers_root` is a Merkle root over all shards' `ChunkHashHeight` values. The proof for shard 1's chunk at tree position 1 is a valid path that makes `compute_root_from_path(path, hash(shard1_chunk)) == chunk_headers_root`. `verify_path` only checks this equality: [1](#0-0) 

It does **not** check the position. The position-aware `verify_path_with_index` exists and is used elsewhere (e.g., `validate_part`), but is absent here. [2](#0-1) 

**No shard_id cross-check exists anywhere in `set_state_header`:** [3](#0-2) 

**Storage.** After all checks pass, the header is written: [4](#0-3) 

`StateHeaderKey(0, sync_hash)` now holds a `ShardStateSyncResponseHeader` whose `chunk.shard_id() == 1` and whose `chunk.prev_state_root()` is shard 1's state root.

**Downstream corruption.** `set_state_part` reads the stored header and uses `chunk.prev_state_root()` (shard 1's root) to validate incoming parts: [5](#0-4) 

`apply_state_part` then installs shard 1's trie data into shard 0's flat storage. `set_state_finalize` uses the same stored header: [6](#0-5) 

### Impact Explanation

A syncing node that accepts the malicious header will install shard 1's state as shard 0's state. The node's shard 0 flat storage and trie will be populated with the wrong data. All subsequent queries, transaction execution, and chunk production for shard 0 on that node will operate against the wrong state root, producing invalid results or causing the node to diverge from the canonical chain.

### Likelihood Explanation

Requires a multi-shard network (4 shards in mainnet). The attacker must be a peer the syncing node contacts for state sync — any node on the network qualifies. All data needed to construct the malicious header (shard 1's chunk, its Merkle proof, shard 0's incoming receipt proofs) is public blockchain data. No validator or operator privilege is needed.

### Recommendation

Add an explicit shard_id binding check immediately after extracting the chunk, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, replace the position-blind `verify_path` call for the chunk proof with `verify_path_with_index`, passing the shard index derived from `shard_id` via `shard_layout.get_shard_index(shard_id)`, to cryptographically bind the proof to the correct position in the Merkle tree. [7](#0-6) 

### Proof of Concept

In `chain/chain/src/state_sync/` add a unit test:

1. Build a two-shard chain with `sync_hash`.
2. Call `get_state_response_header(shard_id=1, sync_hash)` on node 0 to obtain shard 1's legitimate header.
3. Call `set_state_header(shard_id=0, sync_hash, shard1_header)` on node 1.
4. Assert the call returns `Ok(())` (demonstrating the missing check).
5. Read back `StateHeaderKey(0, sync_hash)` from the DB and assert `stored_header.chunk_prev_state_root() == shard1_state_root` (demonstrating the corrupted binding). [8](#0-7)

### Citations

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

**File:** chain/chain/src/chain.rs (L2704-2707)
```rust
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
```
