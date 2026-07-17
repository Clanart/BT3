Based on my thorough code analysis, here is the finding:

---

### Title
Missing `chunk.shard_id()` vs. `shard_id` parameter check in `set_state_header` allows cross-shard state root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` from a peer and stores it under `StateHeaderKey(shard_id, sync_hash)`. It validates the chunk's internal consistency and proves the chunk is in the block's Merkle tree, but **never checks that `chunk.shard_id() == shard_id`**. A malicious peer can supply shard_B's chunk (with shard_B's `prev_state_root`) in a response for shard_A, causing the node to store shard_B's state root under shard_A's key and subsequently apply shard_B's state parts to shard_A's trie.

### Finding Description

**Entrypoint:** `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`.

**Guard 1 — `validate_chunk_proofs`** (lines 380–385): [1](#0-0) 

This checks hash consistency, tx root, and outgoing receipts root — all internal to the chunk. It does **not** compare `chunk.shard_id()` against the `shard_id` parameter.

**Guard 2 — `verify_path` against `chunk_headers_root`** (lines 394–403): [2](#0-1) 

`verify_path` computes `compute_root_from_path(path, hash(item)) == root`. It proves the chunk hash is *somewhere* in the Merkle tree, but does **not** verify which leaf index (shard slot) it occupies: [3](#0-2) 

Compare with `verify_path_with_index`, which explicitly checks the leaf index via `verify_path_matches_index`: [4](#0-3) 

`set_state_header` uses `verify_path`, not `verify_path_with_index`. Shard_B's chunk hash is legitimately present in the block's `chunk_headers_root` Merkle tree, so the proof passes even when `shard_id=A`.

**Guard 3 — Receipt proof check** (lines 487–502): [5](#0-4) 

`receipts_hash = hash(ReceiptList(shard_id, receipts))` uses the *parameter* `shard_id` (shard_A). A naive substitution of shard_B's receipt proofs would fail here. However, the attacker can populate `incoming_receipts_proofs` with shard_A's **actual** incoming receipt proofs (all public on-chain data), satisfying this check while the `chunk` field still contains shard_B's chunk.

**No shard_id binding check exists anywhere in the function.** After all guards pass, the header is stored: [6](#0-5) 

`StateHeaderKey(shard_id_A, sync_hash)` now holds a `ShardStateSyncResponseHeader` whose `chunk.prev_state_root()` is shard_B's state root.

**Downstream in `set_state_part`:** [7](#0-6) 

`state_root` is read from the stored header (shard_B's root). `validate_state_part(shard_id_A, &state_root_B, ...)` validates parts against shard_B's trie, and those parts are then applied to shard_A's trie in `set_state_finalize`. [8](#0-7) 

### Impact Explanation

The node installs shard_B's state into shard_A's trie. After `set_state_finalize`, shard_A's `ChunkExtra.state_root` reflects shard_B's state. All subsequent chunk applications for shard_A operate on the wrong state, producing wrong execution results, wrong outgoing receipts, and wrong state roots — permanently diverging from the canonical chain.

### Likelihood Explanation

Requires a malicious state-sync peer (any node can serve state sync responses) and the ability to construct a crafted `ShardStateSyncResponseHeader`. All required data (shard_B's chunk, its Merkle proof, shard_A's receipt proofs) is publicly available on-chain. No validator or privileged role is needed. The attack is feasible for any peer that the syncing node contacts.

### Recommendation

Add an explicit shard_id binding check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD THIS:
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, consider switching the Merkle proof verification to `verify_path_with_index` using the shard's index in the layout, to cryptographically bind the chunk to its shard slot.

### Proof of Concept

```rust
// In a cargo test using TestEnv:
// 1. Get a valid ShardStateSyncResponseHeader for shard_id=1
let header_for_shard1 = client[0].chain.state_sync_adapter
    .get_state_response_header(ShardId::new(1), sync_hash).unwrap();

// 2. Call set_state_header with shard_id=0 but header for shard 1
let result = client[1].chain.state_sync_adapter
    .set_state_header(ShardId::new(0), sync_hash, header_for_shard1.clone());

// 3. Assert it succeeds (no shard_id check exists)
assert!(result.is_ok());

// 4. Read back the stored header and assert the chunk's shard_id is wrong
let stored = client[1].chain.state_sync_adapter
    .get_state_header(ShardId::new(0), sync_hash).unwrap();
assert_eq!(stored.cloned_chunk().shard_id(), ShardId::new(1)); // stored under shard 0 key
```

The receipt proof check (step 4e) requires the attacker to supply shard_A's actual receipt proofs rather than shard_B's, but all such data is publicly available from the chain, making the full exploit constructible without any privileged access.

### Citations

**File:** chain/chain/src/validate.rs (L22-67)
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

**File:** chain/chain/src/state_sync/adapter.rs (L487-503)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
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

**File:** chain/chain/src/chain.rs (L2703-2708)
```rust
    ) -> Result<(), Error> {
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
        chain_update.commit()?;
```
