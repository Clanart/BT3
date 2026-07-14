### Title
Missing Duplicate-Key Guard in `batch_insert` Fast Path Corrupts DataLayer Merkle Tree — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the `KeyAlreadyPresent` guard when the tree already has more than one leaf. An attacker (or buggy caller) who supplies a batch containing a `KeyId` already present in the tree will silently insert a second leaf with the same key, producing two structurally-connected leaves with identical keys, a corrupted Merkle root, and a desynchronised `block_status_cache`.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) correctly rejects duplicate keys: [1](#0-0) 

`batch_insert` uses `insert` only for the bootstrap phase when `leaf_count <= 1`: [2](#0-1) 

Once `leaf_count > 1`, the bootstrap phase is skipped entirely and every item in the batch is written directly through `insert_entry_to_blob` with **no `contains_key` check**: [3](#0-2) 

`insert_entry_to_blob` unconditionally calls `block_status_cache.add_leaf`: [4](#0-3) 

`add_leaf` calls `HashMap::insert`, which **silently overwrites** the existing `key_to_index` entry without signalling an error: [5](#0-4) 

After the call:
- The blob contains **two** structurally-connected leaf nodes with the same `KeyId`.
- `key_to_index` maps that key to only the **new** index (last-write-wins).
- The old leaf remains reachable from the tree's internal nodes and contributes to the Merkle root hash.
- `free_indexes` does **not** include the old leaf's index, so it is never reclaimed.

`check_integrity` will detect the divergence (`leaf_count` from blob iteration ≠ `key_to_index.len()`), but only if explicitly called after the fact: [6](#0-5) 

---

### Impact Explanation

The Merkle root is derived from the actual tree structure, which now contains a phantom duplicate leaf. Any root published after this operation is cryptographically wrong. Proofs of inclusion generated via `get_proof_of_inclusion` will be anchored to this corrupted root. Proofs of exclusion for keys near the duplicate may also be invalid. This directly satisfies the **High** impact criterion: *DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state*.

---

### Likelihood Explanation

`batch_insert` is a `pub` function exposed directly to Python callers via `py_batch_insert`: [7](#0-6) 

Any DataLayer client that submits a store-update batch containing a key already present in the tree (e.g., intending an update but calling `batch_insert` instead of `upsert`) will silently corrupt the tree. No privileged access is required; the only precondition is that the tree already holds more than one leaf, which is the normal operating state.

---

### Recommendation

Add a `contains_key` guard at the top of the fast-path loop in `batch_insert`, mirroring the check in `insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    // ... existing logic
}
```

Alternatively, make `add_leaf` return an error (or panic) when `key_to_index.insert` returns `Some(old_index)`, so the invariant is enforced at the cache layer regardless of which insertion path is used.

---

### Proof of Concept

```rust
use chia_datalayer::{MerkleBlob, KeyId, ValueId, InsertLocation, Hash};

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Pre-condition: leaf_count > 1 so the fast path is taken
for i in 1i64..=3 {
    blob.insert(KeyId(i), ValueId(i), &sha256_num(&i), InsertLocation::Auto {}).unwrap();
}
assert_eq!(blob.block_status_cache.leaf_count(), 3);

// batch_insert with a key that already exists (key 1)
let new_hash = sha256_num(&99i64);
blob.batch_insert(vec![((KeyId(1), ValueId(99)), new_hash)]).unwrap();
// No error returned — duplicate silently accepted

// Tree is now corrupted: two leaves with KeyId(1) in the blob
// check_integrity detects the divergence
blob.check_integrity().expect_err("integrity check must fail due to duplicate key");
```

The `check_integrity` call will return `Error::IntegrityKeyToIndexCacheIndex` (the old leaf's index no longer matches the cache) and/or `Error::IntegrityKeyToIndexCacheLength` (blob leaf count 4 ≠ cache entry count 3), confirming the invariant violation.

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L369-371)
```rust
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-585)
```rust
        if self.block_status_cache.leaf_count() <= 1 {
            for _ in 0..2 {
                let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                    return Ok(());
                };
                self.insert(key, value, &hash, InsertLocation::Auto {})?;
            }
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L587-603)
```rust
        for ((key, value), hash) in keys_values_hashes {
            let new_leaf_index = self.get_new_index();
            let new_block = Block {
                metadata: NodeMetadata {
                    node_type: NodeType::Leaf,
                    dirty: false,
                },
                node: Node::Leaf(LeafNode {
                    parent: Parent(None),
                    hash,
                    key,
                    value,
                }),
            };
            self.insert_entry_to_blob(new_leaf_index, &new_block)?;
            indexes.push(new_leaf_index);
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-867)
```rust
        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1026)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1503-1516)
```rust
    #[pyo3(name = "batch_insert")]
    pub fn py_batch_insert(
        &mut self,
        keys_values: Vec<(KeyId, ValueId)>,
        hashes: Vec<Hash>,
    ) -> PyResult<()> {
        if keys_values.len() != hashes.len() {
            Err(Error::UnmatchedKeysAndValues(
                keys_values.len(),
                hashes.len(),
            ))?;
        }

        self.batch_insert(zip(keys_values, hashes).collect())?;
```
