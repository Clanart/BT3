The key question is whether the `verify_path` check at step 3a acts as an implicit shard-binding guard. Let me verify the exact Merkle proof semantics.

### Title
Missing shard-identity check in `set_state_header` allows cross-shard state-root substitution — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded chunk belongs to shard A and stores it under `StateHeaderKey(shard_id=B, sync_hash)` without ever asserting `chunk.shard_id() == shard_id`. The Merkle-inclusion check used to bind the chunk to the block (`verify_path`) is position-agnostic: it proves the chunk hash appears *somewhere* in `chunk_headers_root`, not that it occupies shard B's leaf index. A malicious peer can therefore craft a structurally valid header for shard A and deliver it as the response to a shard B header request, causing the victim to finalize shard B's state from shard A's trie root.

---

### Finding Description

**Entrypoint**: `set_state_header` in `chain/chain/src/state_sync/adapter.rs` lines 368–531, called by the state-sync downloader after a peer delivers a `ShardStateSyncResponseHeader`.

**Missing guard**: No line in the function compares `chunk.shard_id()` to the caller-supplied `shard_id` parameter. [1](#0-0) 

**Why `verify_path` does not substitute for the missing check**:

`verify_path` is defined as:
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
``` [2](#0-1) 

It is a pure hash-chain computation with no index/position awareness. The position-aware variant `verify_path_with_index` (which additionally calls `verify_path_matches_index`) exists and is used elsewhere (e.g., chunk-part validation), but is **not** used here. [3](#0-2) 

**How `chunk_headers_root` is built**: it is a Merkle tree over `[ChunkHashHeight(shard_0_hash, h0), ChunkHashHeight(shard_1_hash, h1), ...]` ordered by shard index. [4](#0-3) 

For a 2-shard block the proof for shard A (index 0) is `[{hash: H(shard_B_chunk_height), direction: Right}]`. Feeding this proof and shard A's chunk hash into `verify_path` against `chunk_headers_root` yields `combine_hash(H(shard_A), H(shard_B)) == chunk_headers_root` — which is **true**. The check passes even though the proof encodes position 0 (shard A), not position 1 (shard B). [5](#0-4) 

**Receipt-proof step does not block the attack**: step 4 hashes receipts with the caller-supplied `shard_id` (B), not with the chunk's own shard id. A malicious peer who has chain access can supply valid shard-B incoming-receipt proofs independently of which chunk body is embedded. [6](#0-5) 

**Storage**: the header is written under `StateHeaderKey(shard_id=B, sync_hash)` with shard A's `prev_state_root` inside. [7](#0-6) 

---

### Impact Explanation

**`set_state_part`** reads the stored header and extracts `prev_state_root` from the embedded chunk to validate incoming parts: [8](#0-7) 

All parts are validated against shard A's state root, so the attacker can supply shard A's actual trie parts and they will pass.

**`set_state_finalize`** derives `shard_uid` from the caller-supplied `shard_id` (B) but applies the chunk using `chunk_header.prev_state_root()` (shard A's root): [9](#0-8) 

The result is that shard A's trie is installed under shard B's `shard_uid`. After sync, shard B has wrong account balances, wrong contract state, and wrong storage — permanently, until the node re-syncs.

---

### Likelihood Explanation

Any node participating in the P2P network can serve state-sync responses; no validator or operator privilege is required. The attacker only needs to be a peer the victim connects to during state sync. All inputs needed to craft the attack (shard A's chunk, its Merkle proof, shard B's receipt proofs) are publicly available on-chain. The construction requires no cryptographic forgery.

---

### Recommendation

Add an explicit shard-identity assertion immediately after extracting the chunk, before any other validation:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be inserted at approximately line 378 of `chain/chain/src/state_sync/adapter.rs`, after `let chunk = shard_state_header.cloned_chunk();` and before `validate_chunk_proofs`. [10](#0-9) 

Optionally, also replace the bare `verify_path` call with `verify_path_with_index` (supplying the shard index derived from `shard_id`) to make the position binding cryptographically explicit, consistent with how chunk-part validation works. [5](#0-4) 

---

### Proof of Concept

Write a test-loop state-sync integration test with 2 shards:

1. From node 0, call `get_state_response_header(shard_id=0, sync_hash)` to obtain shard 0's legitimate header (call it `header_A`).
2. From node 0, obtain valid shard 1 incoming-receipt proofs and substitute them into `header_A`'s `incoming_receipts_proofs` field, producing `crafted_header`.
3. On node 1, call `set_state_header(shard_id=1, sync_hash, crafted_header)`.
4. Assert the call returns `Ok(())`.
5. Read back the stored header via `get_state_header(shard_id=1, sync_hash)` and assert `stored_header.cloned_chunk().shard_id() == 0` (not 1) — confirming shard A's chunk is stored under shard B's key.
6. Assert `stored_header.cloned_chunk().take_header().take_inner().prev_state_root() != shard_1_actual_prev_state_root`.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-385)
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

**File:** chain/chain/src/state_sync/adapter.rs (L486-493)
```rust
                };
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
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

**File:** core/primitives/src/merkle.rs (L112-119)
```rust
/// Verify merkle path for given item and corresponding path.
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

**File:** core/primitives/src/block.rs (L815-822)
```rust
    pub fn compute_chunk_headers_root(&self) -> (CryptoHash, Vec<MerklePath>) {
        merklize(
            &self
                .iter()
                .map(|chunk| ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()))
                .collect_vec(),
        )
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
