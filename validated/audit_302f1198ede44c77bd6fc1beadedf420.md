### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation, Silently Corrupting the DataLayer Merkle Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` applies duplicate-key and duplicate-hash guards only to the first two items in a batch. All subsequent items are written directly to the blob and cache via `insert_entry_to_blob` without any collision check. When a batch contains a key (or hash) that already exists in the tree — either from a prior insert or from a duplicate within the same batch — the `BlockStatusCache` is silently overwritten, the old leaf node is orphaned in the blob, and the computed Merkle root becomes incorrect. This allows untrusted input to corrupt tree state and produce forged or invalid inclusion proofs.

### Finding Description

`MerkleBlob::insert` enforces two guards before writing a leaf:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` for only the first two items (the bootstrap case when the tree has ≤ 1 leaf):

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining items (index 3 and beyond) enter a fast path that calls `insert_entry_to_blob` directly, with **no key or hash collision check**:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which performs an unconditional `HashMap::insert` — silently overwriting any existing entry for the same key or hash:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [4](#0-3) 

The dispatch in `insert_entry_to_blob` that routes to `add_leaf`: [5](#0-4) 

The result is a split state: the blob contains two leaf nodes sharing the same `KeyId` (or `Hash`), but the cache only tracks the second one. The first leaf is structurally orphaned yet still physically present in the blob. Because internal-node hashes are computed from child hashes, the Merkle root derived from this corrupted tree is wrong.

The Python binding `py_batch_insert` exposes this path directly to callers: [6](#0-5) 

### Impact Explanation

Corrupting the Merkle root means:

1. **Forged inclusion proofs**: `get_proof_of_inclusion` for the surviving (cache-tracked) leaf will traverse a tree whose root hash does not reflect the true committed state, producing a proof that `valid()` accepts against a wrong root.
2. **Invisible orphan leaves**: The orphaned leaf is unreachable via the cache but still occupies blob space and participates in internal-node hash computation, causing the root to diverge from any externally committed value.
3. **Integrity check bypass**: `check_integrity` compares `leaf_count` against `key_to_index_cache_length`; because the orphan leaf is not in the cache, the counts disagree and the check fails — but only after the corruption has already occurred and been committed.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots and lets untrusted input prove invalid state.**

### Likelihood Explanation

`batch_insert` is the primary high-throughput insertion API and is called by the Python DataLayer sync path. Any caller that supplies a batch containing a key already present in the tree — or two identical keys within the same batch — triggers the bug. No privilege is required; the attacker only needs to control the contents of the batch passed to `batch_insert`.

### Recommendation

Add the same guards that `insert` applies to every item in the fast path of `batch_insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing fast-path logic
}
```

Alternatively, accumulate the batch keys/hashes into a temporary `HashSet` before writing any blocks, and reject the entire batch if any collision is detected — both against the existing tree and within the batch itself.

### Proof of Concept

```rust
#[test]
fn test_batch_insert_duplicate_key_corrupts_tree() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    blob.check_integrity_on_drop = false;

    // Pre-populate with 2 leaves so the fast path is active for item 3+
    for i in 0i64..2 {
        blob.insert(KeyId(i), ValueId(i), &sha256_num(&i), InsertLocation::Auto {}).unwrap();
    }

    // Batch with 3 items; item index 2 (the 3rd) reuses KeyId(0) — already in tree
    let batch = vec![
        ((KeyId(10), ValueId(10)), sha256_num(&10i64)),
        ((KeyId(11), ValueId(11)), sha256_num(&11i64)),
        ((KeyId(0),  ValueId(99)), sha256_num(&99i64)), // duplicate key, bypasses check
    ];

    // batch_insert succeeds — no error returned
    blob.batch_insert(batch).unwrap();

    // Tree is now corrupted: two leaf nodes with KeyId(0) in the blob,
    // cache points only to the second; root hash is wrong.
    // check_integrity() will fail due to leaf_count vs cache length mismatch.
    assert!(blob.check_integrity().is_err());
}
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L369-374)
```rust
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1503-1519)
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

        Ok(())
    }
```
