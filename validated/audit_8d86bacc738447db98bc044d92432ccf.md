### Title
Missing shard-position binding in `set_state_header` chunk-proof check allows cross-shard state installation — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` verifies that the supplied chunk is included in the block's `chunk_headers_root` using `verify_path`, but `verify_path` is a position-agnostic membership check. It does not verify that the chunk sits at the index corresponding to the requested `shard_id`. There is also no explicit guard asserting `chunk.shard_id() == shard_id`. A malicious peer can therefore supply a `ShardStateSyncResponseHeaderV2` whose embedded chunk belongs to shard B while the syncing node requested shard A, and the header will be accepted and persisted under `StateHeaderKey(shard_id=A, sync_hash)`. Subsequent `set_state_part` and `apply_state_part` calls then install shard B's trie state for shard A.

---

### Finding Description

**Root cause — `verify_path` is position-agnostic**

`verify_path` in `core/primitives/src/merkle.rs` computes the Merkle root by folding the path items and comparing to the expected root:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
``` [1](#0-0) 

The position-aware variant, `verify_path_with_index`, calls `verify_path_matches_index` to enforce that the path directions match the expected leaf index:

```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash, path: &MerklePath, item: T,
    part_idx: u64, num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
}
``` [2](#0-1) 

`set_state_header` uses only the position-agnostic `verify_path`:

```rust
if !verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
) { ... }
``` [3](#0-2) 

**No `chunk.shard_id() == shard_id` guard**

Nowhere in `set_state_header` is there a check that `chunk.shard_id()` equals the `shard_id` argument. The function extracts the chunk, validates its internal proofs, verifies Merkle membership (position-agnostic), validates receipts using the argument `shard_id` (not `chunk.shard_id()`), and then writes the header under the argument key:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [4](#0-3) 

**`validate_chunk_proofs` does not check shard identity**

`validate_chunk_proofs` only verifies the chunk's internal hash, transaction root, and outgoing-receipts root. It never compares `chunk.shard_id()` to any expected value: [5](#0-4) 

**Why the Merkle proof for shard B passes when shard A is requested**

In a 2-shard block, `chunk_headers_root = combine_hash(H_A, H_B)`. Chunk A's proof is `[Right: H_B]` and chunk B's proof is `[Left: H_A]`. Both proofs reconstruct the same root:

- Chunk A: `combine_hash(H_A, H_B)` ✓  
- Chunk B: `combine_hash(H_A, H_B)` ✓

An attacker submitting chunk B with its own valid proof `[Left: H_A]` as the header for shard A will pass the `verify_path` check, because the computed root equals `chunk_headers_root` regardless of which shard's chunk is presented.

**Downstream propagation into `set_state_part`**

`set_state_part` reads the stored header, extracts `prev_state_root` from the embedded chunk (shard B's state root), and validates incoming parts against it:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// validates parts against shard B's state_root, for shard_id = A
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
``` [6](#0-5) 

The attacker supplies valid state parts for shard B; they pass validation against shard B's state root and are stored under `StatePartKey(sync_hash, shard_id=A, part_id)`. `apply_state_part` then installs shard B's trie nodes into shard A's trie slot.

---

### Impact Explanation

The syncing node installs shard B's complete trie state as shard A's state. All subsequent block processing for shard A operates on the wrong state root, producing incorrect execution results, incorrect `ChunkExtra`, and diverging from the honest chain. Flat-state deltas are written to the wrong `ShardUId`. The node cannot recover without a full re-sync.

---

### Likelihood Explanation

Any peer that the syncing node contacts for state sync can mount this attack. No validator or operator privileges are required. The attacker only needs to serve a response to a `StateHeaderRequest` for shard A with a header whose chunk field is taken from shard B of the same block. All required data (chunk B, its Merkle proof, shard A's incoming receipts, shard B's state root node and state parts) is publicly available on-chain.

---

### Recommendation

Add an explicit shard-identity guard immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {} != requested shard_id {}",
        chunk.shard_id(), shard_id
    )));
}
```

Additionally, replace the plain `verify_path` call with `verify_path_with_index`, passing the shard index derived from `shard_id` and the total number of shards, so that the Merkle proof is bound to the correct leaf position in `chunk_headers_root`. [3](#0-2) 

---

### Proof of Concept

1. Set up a two-shard test environment with `sync_hash` pointing to a finalized block.
2. From the honest node, obtain `header_B = get_state_response_header(shard_B, sync_hash)` (chunk for shard B, valid Merkle proof for shard B's position, shard B's state root node).
3. Obtain valid incoming-receipt proofs for shard A from the same block range (public chain data).
4. Construct a `ShardStateSyncResponseHeaderV2` with `chunk = header_B.chunk`, `chunk_proof = header_B.chunk_proof`, and `incoming_receipts_proofs` / `root_proofs` taken from shard A's honest header.
5. Call `syncing_node.state_sync_adapter.set_state_header(shard_A, sync_hash, crafted_header)`.
6. Assert the call returns `Ok(())`.
7. Read back the stored header via `get_state_header(shard_A, sync_hash)` and assert `stored_header.chunk().shard_id() == shard_B` (mismatch with the key `shard_A`).
8. Obtain valid state parts for shard B and call `set_state_part(shard_A, sync_hash, ...)` for each; assert all return `Ok(())`.
9. Call `set_state_finalize(shard_A, sync_hash)` and observe that shard A's trie now contains shard B's state.

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

**File:** chain/chain/src/state_sync/adapter.rs (L527-528)
```rust
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

**File:** chain/chain/src/state_sync/adapter.rs (L541-545)
```rust
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
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
