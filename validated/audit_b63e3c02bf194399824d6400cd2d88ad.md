### Title
Missing shard-position binding in `set_state_header` allows cross-shard trie corruption via malicious state sync header — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` validates that a supplied chunk is present in the canonical block's Merkle tree via `verify_path`, but uses the position-agnostic variant that does **not** verify the chunk occupies the leaf index corresponding to the requested `shard_id`. There is also no explicit check that `chunk.shard_id() == shard_id`. A malicious peer can supply a `ShardStateSyncResponseHeader` for shard B that embeds chunk A's data and chunk A's valid Merkle proof; all guards pass, the header is stored under `StateHeaderKey(shard_id=B, sync_hash)`, and subsequent `apply_state_part` calls write shard A's trie nodes and flat-state entries into shard B's `ShardUId` store.

### Finding Description

**Entrypoint**: `StateSyncDownloader::ensure_shard_header` (chain/client/src/sync/state/downloader.rs:44) downloads a `ShardStateSyncResponseHeader` from a peer and sends it to `set_state_header` for validation.

**`set_state_header` validation** (chain/chain/src/state_sync/adapter.rs:368–531) performs five checks:

1. `validate_chunk_proofs(&chunk, ...)` — internal chunk consistency (tx/receipt Merkle paths). Passes for any well-formed chunk.
2. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof, &ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` — verifies the chunk hash appears in the block's chunk-header Merkle tree.
3. `verify_path` for `prev_chunk_header` — same positional blindness.
4. Receipt-proof chain checks — uses the `shard_id` **parameter** (B) to hash receipts; valid canonical proofs for shard B satisfy this.
5. `validate_state_root_node(state_root_node, chunk_inner.prev_state_root())` — validates the state root node against the chunk's own `prev_state_root`.

**The gap**: Check 2 uses `verify_path` (core/primitives/src/merkle.rs:113–114), which only computes `compute_root_from_path(path, item_hash) == root`. It does **not** verify the leaf index. The stronger `verify_path_with_index` (merkle.rs:121–128), which calls `verify_path_matches_index` to confirm the Left/Right directions match a specific position, is **not** used here. There is no `chunk.shard_id() == shard_id` guard anywhere in `set_state_header`.

**Merkle tree structure**: `chunk_headers_root` is a positional Merkle tree over all chunks indexed by shard. For a 2-shard block, chunk A at position 0 has proof `[Right(hash_chunk_B)]` and chunk B at position 1 has proof `[Left(hash_chunk_A)]`. Both proofs compute `combine_hash(hash_chunk_A, hash_chunk_B) = root`. `verify_path` accepts chunk A's proof even when the caller claims `shard_id=B`.

**Attack construction** (all data is public, no validator privilege required):
- Chunk A's full data + its valid Merkle proof: obtainable from any honest node tracking shard A.
- Shard B's valid incoming-receipt proofs: obtainable from any honest node.
- A valid `state_root_node` for shard A's `prev_state_root`: obtainable from any node tracking shard A.

The malicious peer assembles these into a `ShardStateSyncResponseHeader` claiming `shard_id=B`. All five checks in `set_state_header` pass.

**Storage path after acceptance**:

- Header stored under `StateHeaderKey(shard_id=B, sync_hash)` (adapter.rs:527).
- `run_state_sync_for_shard` reads `state_root = header.chunk_prev_state_root()` — this is shard A's state root (shard.rs:76).
- State parts are validated against shard A's state root — they pass.
- `apply_state_part(shard_id=B, state_root=shard_A_root, ...)` (runtime/mod.rs:1501–1528) calls `get_shard_uid_from_epoch_id(shard_id=B, epoch_id)` → shard B's `ShardUId`, then writes shard A's trie nodes via `tries.apply_all(&trie_changes, shard_uid_B, ...)` and shard A's flat-state entries via `flat_state_delta.apply_to_flat_state(..., shard_uid_B)`.

### Impact Explanation

Shard B's trie store and flat storage are populated with shard A's account data. All subsequent reads of shard B's state (balances, contract storage, nonces) return shard A's values. The corruption is committed to disk and persists across restarts. `set_state_finalize` then applies shard A's chunk transactions against this corrupted state, compounding the damage.

### Likelihood Explanation

Any peer on the NEAR P2P network can serve state sync responses. No validator key or privileged role is required. The attacker only needs to be selected as the state sync source for the victim node, which is a normal network event during epoch transitions. All inputs needed to construct the malicious header are publicly available on-chain.

### Recommendation

In `set_state_header`, add an explicit shard-id binding check immediately after extracting the chunk:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Alternatively, replace `verify_path` with `verify_path_with_index` using `shard_id` as the leaf index, so the Merkle proof is bound to the correct position in the chunk-header tree.

### Proof of Concept

The missing guard is confirmed by the absence of any `chunk.shard_id() == shard_id` check in `set_state_header`: [1](#0-0) 

`verify_path` does not check leaf position: [2](#0-1) 

The position-aware variant exists but is not used here: [3](#0-2) 

`apply_state_part` derives `shard_uid` from the `shard_id` parameter (B), not from the chunk, and writes all trie/flat-state changes to that UID: [4](#0-3) 

`state_root` passed to `apply_state_part` comes directly from the (malicious) stored header: [5](#0-4)

### Citations

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

**File:** core/primitives/src/merkle.rs (L113-118)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
```

**File:** core/primitives/src/merkle.rs (L121-128)
```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash,
    path: &MerklePath,
    item: T,
    part_idx: u64,
    num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
```

**File:** chain/chain/src/runtime/mod.rs (L1516-1527)
```rust
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
```

**File:** chain/client/src/sync/state/shard.rs (L75-77)
```rust
    let header = downloader.ensure_shard_header(shard_id, sync_hash, cancel.clone()).await?;
    let state_root = header.chunk_prev_state_root();
    let num_parts = header.num_state_parts();
```
