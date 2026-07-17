Let me look at the `verify_path` implementation and the state_request_actor entrypoint to complete the analysis.

### Title
Missing shard_id binding check in `set_state_header` allows cross-shard state header confusion — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` in `ChainStateSyncAdapter` never asserts that the `ShardChunk` embedded in the supplied `ShardStateSyncResponseHeader` actually belongs to the requested `shard_id`. The `verify_path` call used to anchor the chunk to the canonical chain is **position-unaware**: it only checks that `compute_root_from_path(path, hash(item)) == root`, not that the item sits at the index that corresponds to `shard_id`. A malicious peer can therefore supply a header whose chunk belongs to shard X together with a valid merkle proof for shard X's position, pass every validation gate in `set_state_header`, and have the header stored under the key `StateHeaderKey(shard_id=Y, sync_hash)`. Subsequent state-part validation then uses shard X's `prev_state_root` as the expected root for shard Y, and if the attacker also serves the state parts, the syncing node installs shard X's trie as shard Y's state.

---

### Finding Description

**Entrypoint**: `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`.

The function performs five checks before persisting the header:

**Check 1 — `validate_chunk_proofs`** [1](#0-0) 

Validates internal chunk consistency (header hash, tx root, receipts root). It contains no comparison of `chunk.shard_id()` against the `shard_id` parameter. [2](#0-1) 

**Check 2 — `verify_path` for the chunk** [3](#0-2) 

Verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is somewhere in the merkle tree rooted at `sync_prev_block_header.chunk_headers_root()`. The primitive used is the **position-unaware** `verify_path`:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
``` [4](#0-3) 

This is distinct from `verify_path_with_index`, which additionally calls `verify_path_matches_index` to confirm the path encodes the correct leaf index. [5](#0-4) 

Because `chunk_headers_root` is the merkle root over **all** shards' `ChunkHashHeight` values, a valid proof for shard X's chunk at position X passes `verify_path` regardless of what `shard_id` (Y) was requested.

**Check 3 — `verify_path` for `prev_chunk_header`** [6](#0-5) 

Same position-unaware primitive. The attacker can supply shard Y's `prev_chunk_header` (with shard Y's valid proof) alongside shard X's chunk, so the receipt-range boundary (`prev_chunk_header.height_included()`) matches shard Y's expected range.

**Check 4 — Receipt proof checks** [7](#0-6) 

Uses `shard_id` (Y) from the call parameter, not from the chunk:
```rust
let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
``` [8](#0-7) 

The attacker supplies shard Y's on-chain receipt proofs, which are public data. These pass independently of which chunk is embedded.

**Check 5 — `validate_state_root_node`** [9](#0-8) 

Validates the `state_root_node` against `chunk_inner.prev_state_root()` — the state root from the **embedded chunk** (shard X), not from the canonical chain's shard Y slot. The attacker supplies shard X's genuine `state_root_node`, which passes.

**Storage** — the header is then written under `StateHeaderKey(shard_id=Y, sync_hash)`: [10](#0-9) 

There is no `chunk.shard_id() == shard_id` assertion anywhere in the function.

---

### Impact Explanation

After the corrupted header is installed, `set_state_part` reads the stored header to obtain the expected `state_root`:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
``` [11](#0-10) 

`state_root` is now shard X's `prev_state_root`. If the attacker also serves state parts for shard X (valid against shard X's root), `validate_state_part(shard_id=Y, &state_root_X, ...)` passes, and the syncing node installs shard X's trie under shard Y's identity. The node then operates with a wrong committed state for shard Y.

---

### Likelihood Explanation

The attack requires the adversary to be a peer that can respond to peer-to-peer state sync requests (no validator or operator privilege needed). All data required to construct the crafted header — shard X's chunk body, merkle proofs, shard Y's receipt proofs — is publicly available on-chain. The attacker must also serve the matching state parts, which is feasible if they control the responding peer for both the header and part phases. Nodes using external-storage state sync (S3/GCS) are not affected; nodes using P2P state sync are the target surface.

---

### Recommendation

Add an explicit shard-id binding check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, consider replacing the position-unaware `verify_path` call with `verify_path_with_index` (already used in `validate_part` for encoded chunk parts) so that the merkle proof is also required to encode the correct leaf index corresponding to `shard_id`'s position in the block's chunk array.

---

### Proof of Concept

In a two-shard test-loop environment:

1. Obtain `sync_hash` and the canonical block headers.
2. Fetch shard 0's `ShardChunk` and its merkle proof at position 0 in `chunk_headers_root` (from `compute_state_response_header` with `shard_id=0`).
3. Fetch shard 1's `prev_chunk_header`, its merkle proof, its incoming receipt proofs, and its root proofs (from the chain store).
4. Fetch shard 0's `state_root_node`.
5. Construct `ShardStateSyncResponseHeader::V2(ShardStateSyncResponseHeaderV2 { chunk: chunk_shard0, chunk_proof: proof_shard0, prev_chunk_header: Some(prev_header_shard1), prev_chunk_proof: Some(proof_prev_shard1), incoming_receipts_proofs: receipts_shard1, root_proofs: root_proofs_shard1, state_root_node: node_shard0 })`.
6. Call `state_sync_adapter.set_state_header(shard_id=1, sync_hash, crafted_header)`.
7. Assert `Ok(())` is returned.
8. Read back the stored header and assert `stored_header.cloned_chunk().shard_id() == ShardId::new(0)` while `shard_id == ShardId::new(1)` — confirming the shard_id binding is corrupted.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L379-385)
```rust
        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
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

**File:** chain/chain/src/state_sync/adapter.rs (L416-425)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L447-503)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L512-523)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/state_sync/adapter.rs (L541-543)
```rust
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
```

**File:** chain/chain/src/validate.rs (L22-66)
```rust
pub fn validate_chunk_proofs(
    chunk: &ShardChunk,
    epoch_manager: &dyn EpochManagerAdapter,
) -> Result<bool, Error> {
    let correct_chunk_hash = chunk.compute_header_hash();

    // 1. Checking chunk.header.hash
    let header_hash = chunk.header_hash();
    if header_hash != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }

    // 2. Checking that chunk body is valid
    // 2a. Checking chunk hash
    if chunk.chunk_hash() != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }
    let height_created = chunk.height_created();
    let outgoing_receipts_root = chunk.prev_outgoing_receipts_root();
    let (transactions, receipts) = (chunk.to_transactions(), chunk.prev_outgoing_receipts());

    // 2b. Checking that chunk transactions are valid
    let (tx_root, _) = merklize(transactions);
    if &tx_root != chunk.tx_root() {
        byzantine_assert!(false);
        return Ok(false);
    }
    // 2c. Checking that chunk receipts are valid
    if height_created == 0 {
        return Ok(receipts.is_empty() && outgoing_receipts_root == &CryptoHash::default());
    } else {
        let shard_layout = {
            let prev_block_hash = chunk.prev_block_hash();
            epoch_manager.get_shard_layout_from_prev_block(&prev_block_hash)?
        };
        let outgoing_receipts_hashes = Chain::build_receipts_hashes(receipts, &shard_layout)?;
        let (receipts_root, _) = merklize(&outgoing_receipts_hashes);
        if &receipts_root != outgoing_receipts_root {
            byzantine_assert!(false);
            return Ok(false);
        }
    }
    Ok(true)
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
