The code is now fully clear. Let me trace the exact execution path.

**`insert_entry_to_blob` (line 1013-1030):** When called with a `Node::Leaf`, it calls `self.block_status_cache.add_leaf(index, leaf)`.

**`add_leaf` (line 188-193):** Calls `self.key_to_index.insert(leaf.key, index)` — a `HashMap::insert` that **silently overwrites** any existing entry for the same key without returning an error.

**`batch_insert` (line 570-657):** The duplicate-key guard only exists inside `self.insert()` (line 369-371). The `for` loop at line 587 calls `insert_entry_to_blob` directly with **zero duplicate-key validation**.

The vulnerability is real and concrete.

---

### Title
Missing Duplicate-Key Guard in `batch_insert` Tail Loop Corrupts DataLayer Merkle Tree State — (`crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`batch_insert` bypasses the duplicate-key check for all items processed in its bulk `for` loop, allowing a caller to silently overwrite an existing leaf in the blob while leaving the `block_status_cache.key_to_index` mapping pointing to a dangling, parentless node — corrupting the Merkle tree root and cache invariants.

### Finding Description

`insert()` guards against duplicates at lines 369–371: [1](#0-0) 

`batch_insert` only calls `self.insert()` (with that guard) for the last two items popped from the Vec, and only when the tree has ≤ 1 leaf: [2](#0-1) 

All remaining items — and **all items when the tree already has ≥ 2 leaves** — are processed in the unchecked `for` loop: [3](#0-2) 

`insert_entry_to_blob` writes the block to the blob and then calls `add_leaf`, which performs an unconditional `HashMap::insert` that silently overwrites the existing `key → index` mapping: [4](#0-3) [5](#0-4) 

The result after inserting a duplicate key `K1`:
- The **old leaf** for `K1` remains in the blob at its original index, still referenced by its parent internal node.
- The **new leaf** for `K1` is written at a fresh index with `parent: Parent(None)` — a dangling node not connected to the tree.
- `key_to_index` now maps `K1` to the new dangling index, so the old leaf is unreachable via the cache.
- The Merkle root hash is computed from the tree structure (which still contains the old leaf), but the cache reports the new leaf's data — a root/cache mismatch.
- `check_integrity()` will fail with `IntegrityKeyToIndexCacheIndex` or `IntegrityKeyToIndexCacheLength`.

### Impact Explanation
This is a **High** DataLayer impact: the Merkle blob and its `block_status_cache` enter an inconsistent state. Any subsequent proof-of-inclusion or proof-of-exclusion generated from this tree will be based on a corrupted root, allowing forged or invalid state to be proven. The old leaf value persists in the tree structure while the cache reports a different (dangling) node for the same key, causing a root mismatch between the blob and the cache.

### Likelihood Explanation
`batch_insert` is a public method exposed directly through the Python binding `py_batch_insert`: [6](#0-5) 

Any caller — including unprivileged DataLayer clients supplying key-value batches — can trigger this by including a key already present in the tree anywhere in the batch (not just the tail). When the tree has ≥ 2 leaves, every single item in the batch bypasses the check.

### Recommendation
Add a `contains_key` check at the top of the `for` loop in `batch_insert`, mirroring the guard in `insert()`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing logic
}
```

### Proof of Concept

```rust
#[test]
fn test_batch_insert_duplicate_key_corrupts_tree() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Insert two leaves so leaf_count() > 1, bypassing the insert() path entirely
    blob.insert(KeyId(1), ValueId(1), &sha256_num(&1i64), InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(2), ValueId(2), &sha256_num(&2i64), InsertLocation::Auto {}).unwrap();

    // Batch contains KeyId(1) — already present in the tree
    // With leaf_count() == 2, the entire batch goes through the unchecked for loop
    let batch = vec![
        ((KeyId(1), ValueId(99)), sha256_num(&99i64)), // duplicate!
        ((KeyId(3), ValueId(3)), sha256_num(&3i64)),
    ];

    // Should return Err(KeyAlreadyPresent) but currently returns Ok(())
    let result = blob.batch_insert(batch);
    assert!(result.is_err(), "expected KeyAlreadyPresent, got Ok — tree is now corrupted");

    // If it returned Ok, integrity is broken:
    blob.check_integrity().expect_err("tree integrity violated by duplicate key insertion");
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1026)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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
