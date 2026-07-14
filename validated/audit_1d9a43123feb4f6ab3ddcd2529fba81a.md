### Title
`batch_insert` Bypasses Duplicate Key/Hash State Checks, Corrupting DataLayer Merkle Tree Root — (File: crates/chia-datalayer/src/merkle/blob.rs)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that the single-item `insert` enforces. When the tree already holds two or more leaves — the normal production state — every item in a batch is written directly to the blob without any existence check. An attacker (or buggy caller) can silently insert a key that already exists, producing a tree with two leaf nodes for the same key, an incorrect root hash, and ambiguous inclusion proofs.

---

### Finding Description

`insert` (the single-item path) enforces two state guards before writing anything:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` only calls `insert` (with those guards) for the **last two items** when the tree has ≤ 1 leaves. For all remaining items — and for **every** item when the tree already has ≥ 2 leaves — it calls `insert_entry_to_blob` directly, bypassing both guards entirely:

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← guarded
    }
}

for ((key, value), hash) in keys_values_hashes {          // ← NO duplicate check
    let new_leaf_index = self.get_new_index();
    ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

The Python binding `py_batch_insert` exposes this path as a public API, accepting arbitrary caller-supplied keys, values, and hashes with no additional validation beyond a length-match check: [3](#0-2) 

---

### Impact Explanation

When a duplicate key is inserted via `batch_insert`:

1. **Two leaf nodes for the same key** exist in the blob. The root hash is computed over both, so two different values for the same key can each be proven as "included" against the same root — a fundamental DataLayer integrity violation.
2. **`block_status_cache` becomes inconsistent** with the blob: the cache's key-to-index mapping is overwritten to point to the new leaf, orphaning the old one. The blob contains a node that the cache no longer tracks.
3. **`check_integrity()` is only enabled in test builds** (`check_integrity_on_drop: cfg!(test)`), so production trees silently accept the corruption. [4](#0-3) 

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

---

### Likelihood Explanation

The tree only needs ≥ 2 leaves — the normal state for any live DataLayer store — for **all** duplicate checks to be bypassed. The Python binding `py_batch_insert` is a public API reachable by any caller with access to the DataLayer interface. No privileged role, leaked key, or network-level attack is required; passing a batch vector containing a key that already exists in the tree is sufficient.

---

### Recommendation

Add the same state guards to `batch_insert` that `insert` enforces. Before writing any item to the blob, check `block_status_cache.contains_key(key)` and `block_status_cache.contains_leaf_hash(hash)` and return `Err(Error::KeyAlreadyPresent())` / `Err(Error::HashAlreadyPresent())` on violation. Alternatively, pre-scan the entire input batch for internal duplicates and against the existing cache before any blob writes begin.

---

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def h(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 leaves so batch_insert enters the unguarded path
blob.insert(KeyId(1), ValueId(1), h(b"h1"))
blob.insert(KeyId(2), ValueId(2), h(b"h2"))

# batch_insert with KeyId(1) duplicated — bypasses KeyAlreadyPresent check
blob.batch_insert(
    [(KeyId(1), ValueId(99)), (KeyId(3), ValueId(3))],
    [h(b"h_dup"),             h(b"h3")]
)

# Tree now has two leaves for KeyId(1) with different values.
# Both can be proven as "included" against the same (corrupted) root.
root = blob.get_root_hash()
print(f"Corrupted root: {root.hex()}")
# check_integrity() would panic here, but it is disabled in production builds.
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L325-332)
```rust
        let self_ = Self {
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L362-374)
```rust
    pub fn insert(
        &mut self,
        key: KeyId,
        value: ValueId,
        hash: &Hash,
        insert_location: InsertLocation,
    ) -> Result<TreeIndex, Error> {
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-603)
```rust
        if self.block_status_cache.leaf_count() <= 1 {
            for _ in 0..2 {
                let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                    return Ok(());
                };
                self.insert(key, value, &hash, InsertLocation::Auto {})?;
            }
        }

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
