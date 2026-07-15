### Title
`batch_insert` Bypasses Duplicate-Key and Duplicate-Hash Validation, Corrupting DataLayer Merkle Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that the single-item `insert` method enforces. When a batch containing a key (or hash) that already exists in the tree is supplied, the function silently writes a second leaf node to the blob, producing a structurally corrupt Merkle tree whose root hash no longer faithfully represents the committed key-value set. This is directly reachable through the Python binding `py_batch_insert`, which is part of the public DataLayer API.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) opens with two mandatory guards:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` uses `insert` only for the first two items when the tree has ≤ 1 existing leaves. All remaining items are written directly via `insert_entry_to_blob` with no equivalent check:

```rust
// lines 587-603 — no contains_key / contains_leaf_hash guard
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

When the tree already has ≥ 2 leaves the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, so **every** item in the batch bypasses validation.

Consequences of inserting a duplicate key:

1. A second `LeafNode` with the same `KeyId` is written to the blob at a fresh index.
2. `BlockStatusCache::add_leaf` (called inside `insert_entry_to_blob`) uses `HashMap::insert`, which silently overwrites the existing `key_to_index` entry, orphaning the first leaf in the blob.
3. The subtree built from the batch is grafted onto the existing tree via `insert_subtree_at_key`, producing a tree that contains two physical leaves for the same logical key but whose cache only tracks one.
4. `calculate_lazy_hashes` then computes a root hash over this corrupt structure — a root that does not correspond to any valid key-value mapping.

The Python binding `py_batch_insert` passes caller-supplied `keys_values` and `hashes` directly into `batch_insert` with no pre-filtering:

```rust
self.batch_insert(zip(keys_values, hashes).collect())?;
```

---

### Impact Explanation

A corrupt root hash means every subsequent `get_proof_of_inclusion` call produces a proof that either (a) cannot be verified against the committed root, or (b) proves membership of a key-value pair that was never legitimately inserted. Either outcome satisfies the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`py_batch_insert` is part of the public Python API (`MerkleBlob.batch_insert` in `datalayer.pyi`). Any Python caller that constructs a batch containing a key already present in the tree — whether by accident or by deliberate supply of a crafted list — triggers the corruption. No privileged role is required; the function accepts arbitrary `(KeyId, ValueId)` pairs and `Hash` values from the caller.

---

### Recommendation

Add the same guards that `insert` uses before writing each leaf in the fast path:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing write logic
}
```

Alternatively, pre-validate the entire batch against the current cache before writing any node, so the operation remains atomic.

---

### Proof of Concept

1. Create a `MerkleBlob` and insert three leaves individually (so `leaf_count == 3`, bypassing the `<= 1` branch).
2. Call `batch_insert` with a batch whose first entry reuses a `KeyId` already in the tree.
3. Observe that `batch_insert` returns `Ok(())` instead of `Err(KeyAlreadyPresent)`.
4. Call `check_integrity` — it will detect the orphaned leaf or the cache/blob mismatch, confirming tree corruption.
5. Call `get_root_hash` before and after; the root changes despite no legitimate new data being added.

**Relevant code locations:**

`batch_insert` unchecked loop: [1](#0-0) 

`insert` duplicate guards (absent in `batch_insert`): [2](#0-1) 

`batch_insert` conditional that limits validated inserts to the first two items: [3](#0-2) 

Python binding that exposes this to callers: [4](#0-3) 

`BlockStatusCache::add_leaf` (overwrites existing key entry silently): [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1503-1518)
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
```
