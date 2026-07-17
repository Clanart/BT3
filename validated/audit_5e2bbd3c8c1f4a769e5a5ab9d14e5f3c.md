### Title
Missing shard-ID binding in `set_state_header` allows cross-shard state-root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` for a requested shard B but never asserts that the embedded `ShardChunk` belongs to shard B. A malicious state-sync peer can respond to a shard-B request with shard A's chunk (carrying a valid merkle proof for shard A's position in the block's `chunk_headers_root`). All existing guards pass, and the header — including shard A's `prev_state_root` — is persisted under `StateHeaderKey(shard_id=B, sync_hash)`, poisoning every subsequent `validate_state_part` and `apply_state_part` call for shard B.

---

### Finding Description

**Entrypoint:** `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`, called from `chain/client/src/client_actor.rs` when a `ShardStateSyncResponseHeader` message arrives from a peer.

**Guard 1 — `validate_chunk_proofs` (lines 379–385):** [1](#0-0) 

`validate_chunk_proofs` (in `chain/chain/src/validate.rs`) checks only internal chunk consistency: header hash, tx-root, and receipts-root. It never compares `chunk.shard_id()` against the caller-supplied `shard_id`. [2](#0-1) 

**Guard 2 — `verify_path` (lines 394–403):** [3](#0-2) 

`verify_path` is defined as:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
``` [4](#0-3) 

It only checks that the supplied leaf hash reaches the root via the supplied path. It does **not** check which leaf index (shard position) the path encodes. Compare with `verify_path_with_index`, which calls `verify_path_matches_index` to enforce the position: [5](#0-4) 

A valid proof for shard A's chunk at index 0 therefore passes `verify_path` even when the request was for shard B at index 1.

**Missing guard — no `chunk.shard_id() == shard_id` check:**

After both guards, the function proceeds directly to store the header: [6](#0-5) 

The key is `StateHeaderKey(shard_id, sync_hash)` where `shard_id` is the **requested** shard B, but the stored value is shard A's `ShardStateSyncResponseHeader` containing shard A's `prev_state_root`.

**Downstream propagation — `set_state_part` (lines 534–561):** [7](#0-6) 

`set_state_part` reads the stored header for shard B, extracts `prev_state_root` from the embedded chunk (now shard A's root), and validates every incoming state part against it. Parts for shard A's trie will pass; parts for shard B's actual trie will fail or be replaced.

---

### Impact Explanation

The concrete corrupted value is `prev_state_root` stored under `StateHeaderKey(shard_id=B, sync_hash)`. It is shard A's state root instead of shard B's. Every `validate_state_part` and `apply_state_part` call for shard B uses this wrong root, causing the syncing node to install shard A's full trie into shard B's slot. The node then operates with a silently wrong shard-B state, which can cause incorrect execution results, wrong balance/storage reads, and divergence from the canonical chain for all accounts homed on shard B.

---

### Likelihood Explanation

Any peer can act as a state-sync provider — no validator or operator privilege is required. The attacker only needs:
1. Knowledge of shard A's `ShardStateSyncResponseHeader` (public, served by honest nodes).
2. Valid receipt proofs for shard B (public blockchain data, used at lines 488–502 with the requested `shard_id`).
3. Shard A's `height_included` to equal shard B's `height_included` (the common case in a healthy network where all shards produce chunks at the same rate), so that the receipt-chain termination check at line 507 passes. [8](#0-7) 

---

### Recommendation

Add an explicit shard-ID binding check immediately after extracting the chunk, before any other validation:

```rust
// After: let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Alternatively, replace the `verify_path` call at lines 394–403 with `verify_path_with_index`, passing the expected shard index derived from the epoch's shard layout, so that the merkle proof is bound to the correct leaf position.

---

### Proof of Concept

Build a test-loop state-sync integration test with two shards:

1. Produce blocks until a sync point is reached.
2. Have the honest node compute `ShardStateSyncResponseHeader` for shard 0 (`header_A`).
3. Intercept the shard-1 state-sync request and respond with `header_A` (shard 0's header, which has a valid `chunk_proof` for position 0 in `sync_prev_block.chunk_headers_root()`).
4. Provide valid receipt proofs for shard 1 (copied from an honest shard-1 response) and shard 0's `state_root_node`.
5. Call `set_state_header(shard_id=1, sync_hash, header_A)`.
6. Assert the call returns `Err(...)` because `chunk.shard_id() == 0 != 1`.

Without the fix, the call returns `Ok(())` and `DBCol::StateHeaders` contains shard 0's `prev_state_root` keyed under `StateHeaderKey(shard_id=1, sync_hash)`.

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

**File:** chain/chain/src/state_sync/adapter.rs (L505-510)
```rust
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
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
