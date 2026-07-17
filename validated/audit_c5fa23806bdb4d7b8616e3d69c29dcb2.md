Looking at the code carefully across `set_state_header`, `validate_chunk_proofs`, `verify_path`, and `set_state_finalize`.

**Key finding: `verify_path` is position-independent.**

The `chunk_headers_root` Merkle tree is built from all shard chunks ordered by shard index. The check at line 394-403 is:

```rust
verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
)
```

`verify_path` in `core/primitives/src/merkle.rs` line 113 only calls `compute_root_from_path(path, hash(item)) == root`. It verifies the chunk is *somewhere* in the tree — it does **not** verify the chunk is at the leaf position corresponding to `shard_id`. Shard 0's chunk with shard 0's valid proof passes this check even when `shard_id=1` is the parameter.

**No `chunk.shard_id() == shard_id` check exists anywhere in `set_state_header`.**

`validate_chunk_proofs` (lines 22-67 of `chain/chain/src/validate.rs`) only checks internal consistency of the chunk (hash, tx_root, receipts_root) — no shard_id binding to the parameter.

The receipt proof check at line 488 uses the `shard_id` *parameter* (not the chunk's shard_id) for `ReceiptList(shard_id, receipts)`, so an attacker can supply valid shard-1 receipt proofs (publicly derivable from the chain) alongside shard-0's chunk.

The `validate_state_root_node` check at line 514-523 validates the `state_root_node` against `chunk_inner.prev_state_root()` — which is shard 0's state root, consistent with the attacker's shard-0 chunk.

Storage at line 527 then writes `StateHeaderKey(shard_id=1, sync_hash) → shard_0_header`.

---

### Title
Missing shard_id binding in `set_state_header` Merkle proof allows cross-shard state header substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary
`set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded chunk belongs to a different shard than the `shard_id` key parameter. The `verify_path` membership check against `chunk_headers_root` is position-independent and does not enforce that the proven chunk occupies the leaf index corresponding to `shard_id`. No explicit `chunk.shard_id() == shard_id` guard exists anywhere in the function.

### Finding Description
In `set_state_header` (`chain/chain/src/state_sync/adapter.rs` lines 368–532):

1. **`validate_chunk_proofs`** (line 380) checks only internal chunk consistency (hash, tx_root, receipts_root). It does not compare `chunk.shard_id()` to the `shard_id` parameter. [1](#0-0) 

2. **`verify_path` against `chunk_headers_root`** (lines 394–403) verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a member of the Merkle tree — but `verify_path` is purely a hash-path membership check with no position/index enforcement. Shard 0's chunk with shard 0's valid proof satisfies this check even when `shard_id=1`. [2](#0-1) [3](#0-2) 

3. **Receipt proof check** (line 488) hashes receipts against the `shard_id` *parameter*, not the chunk's shard_id. An attacker supplies valid shard-1 receipt proofs (publicly derivable from the chain) alongside shard-0's chunk. [4](#0-3) 

4. **`validate_state_root_node`** (lines 514–523) validates the `state_root_node` against `chunk_inner.prev_state_root()` — which is shard 0's state root, consistent with the attacker's shard-0 chunk. [5](#0-4) 

5. **Storage** (lines 526–529) writes `StateHeaderKey(shard_id=1, sync_hash) → shard_0_header` with no further guard. [6](#0-5) 

### Impact Explanation
After the corrupted header is stored, the node downloads state parts for shard 0's `prev_state_root` (returned by `chunk_prev_state_root()` from the stored header). `set_state_finalize` is then called with `shard_id=1`, reads the stored header, and in `chain_update.rs` `set_state_finalize` (lines 452–568):

- Derives `shard_uid` from `shard_id=1` (line 513–514)
- Calls `apply_chunk` with shard 0's `prev_state_root` and shard 0's transactions/receipts under shard 1's `shard_uid`
- Saves the resulting `ChunkExtra` (with wrong `state_root`) for shard 1 [7](#0-6) 

The exact corrupted value: `ChunkExtra.state_root` for shard 1 is set to the post-application root of shard 0's chunk applied to shard 0's trie, permanently diverging from the honest network's shard 1 state root.

### Likelihood Explanation
Requires the attacker to be an allowed state-sync peer (reachable by the syncing node). State sync sources are selected from network peers, not restricted to validators. The attacker needs access to the honest chain's public data (chunk bodies, receipt proofs) to construct the cross-shard header — all of which is publicly available. No cryptographic secret is required.

### Recommendation
Add an explicit shard_id binding check immediately after extracting the chunk:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be inserted after line 376 (`let chunk = shard_state_header.cloned_chunk();`) and before the `validate_chunk_proofs` call. Additionally, consider replacing the position-independent `verify_path` with `verify_path_with_index` (already used in `shards_manager_actor.rs`) to enforce that the chunk occupies the correct leaf position in the `chunk_headers_root` tree corresponding to `shard_id`. [8](#0-7) 

### Proof of Concept
In a 2-shard test-loop environment:
1. Honest node A produces blocks with shard 0 and shard 1 chunks.
2. Attacker node B intercepts the state sync request for shard 1 from syncing node C.
3. B constructs a `ShardStateSyncResponseHeaderV2` with:
   - `chunk` = shard 0's `ShardChunk` (valid, signed by the real chunk producer)
   - `chunk_proof` = shard 0's Merkle proof from `sync_prev_block.chunks()` at index 0
   - `prev_chunk_header` = shard 0's previous chunk header
   - `prev_chunk_proof` = shard 0's previous Merkle proof
   - `incoming_receipts_proofs` = valid shard-1 incoming receipts (obtained from A)
   - `root_proofs` = valid root proofs for shard-1 receipts (obtained from A)
   - `state_root_node` = shard 0's state root node (consistent with shard 0's `prev_state_root`)
4. B sends this to C as the response to C's shard-1 state header request.
5. Assert `set_state_header` returns `Ok(())`.
6. Assert the stored header's `chunk.shard_id() == 0` while the key is `StateHeaderKey(shard_id=1, sync_hash)`.
7. Apply shard-0 state parts under shard 1, call `set_state_finalize(shard_id=1, sync_hash)`.
8. Assert `ChunkExtra.state_root` for shard 1 on node C does not match node A's `ChunkExtra.state_root` for shard 1.

### Citations

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

**File:** chain/chain/src/state_sync/adapter.rs (L376-385)
```rust
        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

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

**File:** chain/chain/src/state_sync/adapter.rs (L487-492)
```rust
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
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

**File:** chain/chain/src/chain_update.rs (L513-544)
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

        self.chain_store_update.save_chunk(chunk);
```
