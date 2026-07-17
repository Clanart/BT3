### Title
Missing `chunk.shard_id() == shard_id` Guard in `set_state_header` Allows Cross-Shard State Root Corruption — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` for a caller-supplied `shard_id` but never verifies that the embedded chunk actually belongs to that shard. The Merkle inclusion check (`verify_path` against `chunk_headers_root`) proves only that the chunk hash is *somewhere* in the block's chunk tree — it does not verify the shard-position/index. An unprivileged peer can therefore supply shard A's chunk as shard B's state header. After the poisoned header is stored, `set_state_finalize` calls `apply_chunk` with shard B's `shard_uid` but shard A's `prev_state_root()`, writing shard A's post-application state root into shard B's `ChunkExtra.state_root`.

---

### Finding Description

**Entrypoint:** `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`.

The function performs five validation steps before persisting the header:

1. **`validate_chunk_proofs`** — verifies the chunk's internal hash/tx-root/receipt-root consistency. No `shard_id` check. [1](#0-0) 

2. **`verify_path` against `chunk_headers_root`** — proves the chunk's `ChunkHashHeight` is *included* in the block's Merkle tree. The tree covers all shards; `verify_path` does not check the leaf index/position. A valid proof for shard A's chunk passes even when shard B is expected. [2](#0-1) [3](#0-2) 

3. **Receipt proof loop** — hashes receipts as `ReceiptList(shard_id, receipts)` using the *parameter* `shard_id` (shard B). An attacker can supply valid receipt proofs for shard B's incoming receipts (all block data is public) while embedding shard A's chunk. [4](#0-3) 

4. **`validate_state_root_node`** — validates the state root node against `chunk_inner.prev_state_root()` (shard A's root). Passes if the attacker provides a valid node for shard A's state. [5](#0-4) 

5. **Storage** — persists under `StateHeaderKey(shard_id=B, sync_hash)` — the wrong key. [6](#0-5) 

There is **no** `chunk.shard_id() == shard_id` guard anywhere in the function.

**Downstream corruption in `set_state_finalize` / `chain_update.rs`:**

`set_state_finalize` retrieves the stored header for shard B, then calls `apply_chunk` with:
- `shard_uid` derived from shard B's epoch (correct for shard B)
- `chunk_header.prev_state_root()` taken from shard A's chunk (wrong for shard B)
- `transactions` from shard A's chunk (wrong for shard B) [7](#0-6) 

`apply_chunk_postprocessing` then writes the resulting `ChunkExtra` — whose `state_root` is shard A's post-application root — into the DB keyed by shard B's `shard_uid`. [8](#0-7) 

---

### Impact Explanation

The exact corrupted value is `ChunkExtra.state_root` for shard B, which becomes shard A's post-application state root instead of shard B's correct post-application state root. Every subsequent `set_state_finalize_on_height` call for shard B reads this wrong root from `chunk_extra.state_root()` and propagates it forward. [9](#0-8) 

The syncing node's shard B state permanently diverges from the honest network. Any block it produces or endorses for shard B will carry the wrong state root, causing it to be rejected by honest validators.

---

### Likelihood Explanation

The attack requires only publicly available data: shard A's chunk (obtainable via normal state sync), its Merkle proof (computable from the public block), valid receipt proofs for shard B (computable from the public block), and shard A's state parts (obtainable via state sync). No validator, chunk producer, or operator privilege is required. Any peer that a syncing node contacts for state sync can execute this.

---

### Recommendation

Add an explicit shard identity check immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, replace the position-agnostic `verify_path` call with `verify_path_with_index`, supplying the expected shard index derived from the epoch's shard layout, so that the Merkle proof is bound to the correct leaf position. [2](#0-1) [10](#0-9) 

---

### Proof of Concept

```
cargo test --package near-chain -- set_state_header_cross_shard_poison
```

Test outline (two-shard `TestEnv`):

1. Produce several blocks with two shards (shard 0 = A, shard 1 = B).
2. From client[0], call `get_state_response_header(shard_A, sync_hash)` to obtain shard A's legitimate header.
3. Construct a poisoned `ShardStateSyncResponseHeader` using shard A's chunk but with receipt proofs recomputed for shard B's incoming receipts.
4. On client[1], call `state_sync_adapter.set_state_header(shard_B, sync_hash, poisoned_header)` — expect `Ok(())`.
5. Apply shard A's state parts via `apply_state_part(shard_B, shard_A_state_root, ...)`.
6. Call `chain.set_state_finalize(shard_B, sync_hash)`.
7. Assert `chain_store.get_chunk_extra(block_hash, shard_B_uid).state_root() == shard_A_post_root` (not shard B's correct root).

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

**File:** core/primitives/src/merkle.rs (L112-115)
```rust
/// Verify merkle path for given item and corresponding path.
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
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

**File:** chain/chain/src/chain_update.rs (L513-520)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
```

**File:** chain/chain/src/chain_update.rs (L603-612)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let chunk_extra = self.chain_store_update.get_chunk_extra(prev_hash, &shard_uid)?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, *chunk_extra.state_root())?;

        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(*chunk_extra.state_root(), true),
```

**File:** chain/chain/src/spice/chunk_application.rs (L82-84)
```rust
    // `ChunkExtra` marks this shard's apply as done; must share `store_update` with the refcounted writes below.
    store_update.chunk_store_update().set_chunk_extra(block_hash, &shard_uid, &chunk_extra);

```
