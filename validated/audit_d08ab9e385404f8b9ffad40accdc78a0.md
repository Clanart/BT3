Based on my full read of `set_state_header` and the supporting functions, here is the determination:

---

### Title
Missing `chunk.shard_id()` vs caller-supplied `shard_id` cross-check in `set_state_header` allows wrong-shard state header installation — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a caller-supplied `shard_id` and a `ShardStateSyncResponseHeader` but never asserts that the embedded chunk's own shard identity matches. A malicious peer can supply shard 1's chunk (with all valid internal proofs and a valid Merkle inclusion proof for position 1 in the block) while the syncing node passes `shard_id=0`, causing the header to be stored under `StateHeaderKey(shard_id=0, sync_hash)` with shard 1's `prev_state_root`. Subsequent state-part download and trie application then installs shard 1's trie as shard 0's state.

### Finding Description

`set_state_header` performs five validation steps before writing to `DBCol::StateHeaders`:

**Step 1–2 — `validate_chunk_proofs`** [1](#0-0) 

`validate_chunk_proofs` verifies the chunk's internal hash, tx root, and receipts root. It never reads or compares `chunk.shard_id()` against anything. [2](#0-1) 

**Step 3a — `verify_path` against `chunk_headers_root`** [3](#0-2) 

`verify_path` calls `verify_hash` → `compute_root_from_path`, which walks the `MerklePath` Left/Right items and reconstructs the root. It confirms the chunk hash is *somewhere* in the tree but does **not** verify the leaf index (position) corresponds to `shard_id`. [4](#0-3) 

A proof for shard 1's chunk at position 1 passes this check even when `shard_id=0` is supplied, because the function only checks root equality, not positional binding.

**Step 4 — receipt proof validation** uses the caller-supplied `shard_id` (not `chunk.shard_id()`) to hash receipts: [5](#0-4) 

An attacker can supply shard 0's actual on-chain incoming receipts (publicly available) alongside shard 1's chunk. The receipt hashes are computed with `shard_id=0`, so they match the on-chain outgoing-receipts roots for shard 0, and the proof passes.

**Step 5 — `validate_state_root_node`** checks only that the node is internally consistent with `chunk_inner.prev_state_root()` (shard 1's root). No shard identity is checked. [6](#0-5) 

**Storage** — the header is written under the caller-supplied `shard_id`: [7](#0-6) 

There is no line anywhere in `set_state_header` that asserts `chunk.shard_id() == shard_id`.

### Impact Explanation

The stored `StateHeaderKey(shard_id=0, sync_hash)` entry contains shard 1's `prev_state_root`. `set_state_part` reads this header to obtain `state_root` and validates/stores parts against it. [8](#0-7) 

The syncing node therefore downloads and applies shard 1's trie data as shard 0's state, corrupting shard 0's trie on the victim node.

### Likelihood Explanation

The attacker must be a network peer from which the syncing node requests a state header. In NEAR's state sync protocol any reachable peer can serve state responses; no validator or operator key is required. The construction requires only publicly observable on-chain data (shard 1's chunk body, its Merkle proof, shard 0's incoming receipts, shard 1's state root node) — all of which a full node already holds.

### Recommendation

Add an explicit shard identity guard immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be inserted before the `validate_chunk_proofs` call at line 380. [9](#0-8) 

### Proof of Concept

```rust
// Pseudocode for a Rust integration test
let header_for_shard_1 = build_valid_state_header(shard_id=1, sync_hash, &chain);
// header_for_shard_1.chunk.shard_id() == 1
// header_for_shard_1.chunk_proof is valid for position 1 in chunk_headers_root
// incoming_receipts_proofs contains shard 0's actual receipts (hashed with shard_id=0)

let result = adapter.set_state_header(
    /*shard_id=*/ ShardId::new(0),
    sync_hash,
    header_for_shard_1,
);
assert!(result.is_ok()); // all five validation steps pass

let stored = adapter.get_state_header(ShardId::new(0), sync_hash).unwrap();
let stored_root = stored.take_chunk().take_header().take_inner().prev_state_root();
assert_eq!(stored_root, shard_1_prev_state_root); // shard 1's root stored for shard 0
assert_ne!(stored_root, shard_0_prev_state_root); // shard 0's root is absent
```

### Citations

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

**File:** chain/chain/src/state_sync/adapter.rs (L488-488)
```rust
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
```

**File:** chain/chain/src/state_sync/adapter.rs (L513-523)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L527-528)
```rust
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
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
