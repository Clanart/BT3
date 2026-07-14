### Title
Missing Duplicate-Key Guard in `MerkleBlob::batch_insert` Fast Path Corrupts DataLayer Merkle Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` contains a fast path that writes leaf nodes directly to the blob without performing the duplicate-key or duplicate-hash existence checks that `MerkleBlob::insert` enforces. Supplying a key that already exists in the tree (or a repeated key within the same batch) silently overwrites the in-memory cache entry and leaves an orphaned leaf in the blob, corrupting the Merkle tree structure and producing an incorrect root hash. Because the Python binding `py_batch_insert` is directly callable by untrusted DataLayer callers, this is a reachable, attacker-controlled path.

### Finding Description

`MerkleBlob::insert` guards against duplicate keys and duplicate leaf hashes before writing anything:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` has two code paths. When the tree already has two or more leaves, **every** item in the batch goes through the fast path (lines 587–603). When the tree has 0–1 leaves, the last two items are popped and routed through `insert` (which has the guards), but **all remaining items** still go through the fast path:

```rust
// lines 587-603 — no contains_key / contains_leaf_hash check
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block {
        ...
        node: Node::Leaf(LeafNode { parent: Parent(None), hash, key, value }),
    };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` updates `block_status_cache` (calling `add_leaf`, which calls `HashMap::insert`). When a duplicate key is written, `HashMap::insert` silently replaces the old `key → index` mapping. The old leaf node remains in the blob with its original parent pointers intact, but the cache no longer references it. The new leaf is then wired into the tree as a fresh subtree via `insert_subtree_at_key`. The result is:

- An orphaned leaf in the blob that is unreachable from the root but still occupies a slot.
- A cache entry pointing to the new leaf, so `get_proof_of_inclusion` returns a proof for the new leaf only.
- The root hash is recomputed from the new tree shape, silently diverging from any previously committed root.

The Python binding `py_batch_insert` passes caller-supplied `keys_values` and `hashes` directly into `batch_insert` with no additional validation:

```rust
// lines 1503-1518
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [3](#0-2) 

No fuzz target covers `batch_insert` with duplicate or pre-existing keys; the existing fuzz targets only exercise `insert`: [4](#0-3) 

### Impact Explanation

An attacker who can supply a DataLayer batch update containing a key that already exists in the committed tree (or a key repeated within the same batch) can silently corrupt the `MerkleBlob`. The resulting root hash diverges from the true committed state. Any `ProofOfInclusion` generated after the corruption reflects the new, attacker-influenced tree shape, not the original committed data. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state**.

### Likelihood Explanation

`batch_insert` is the primary bulk-insertion API and is called directly from the Python DataLayer node when processing sync updates. A DataLayer store operator or a peer submitting a delta that re-uses an existing key triggers the bug without any special privilege. The condition (tree has ≥ 2 leaves, batch contains a pre-existing key) is easily satisfied in any non-trivial store.

### Recommendation

Add the same existence checks at the top of the fast path in `batch_insert` that `insert` already performs:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    ...
}
```

Alternatively, pre-validate the entire input vector before any writes begin, and add a fuzz target that exercises `batch_insert` with duplicate and pre-existing keys.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def make_hash(n):
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Insert 3 unique keys so the tree has >= 2 leaves
kv = [(KeyId(i), ValueId(i)) for i in range(3)]
hashes = [make_hash(i) for i in range(3)]
blob.batch_insert(kv, hashes)
blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# Now batch_insert with KeyId(0) which already exists — fast path, no guard
kv2 = [(KeyId(0), ValueId(99)), (KeyId(10), ValueId(10)), (KeyId(11), ValueId(11))]
hashes2 = [make_hash(100), make_hash(10), make_hash(11)]
blob.batch_insert(kv2, hashes2)   # succeeds silently
blob.calculate_lazy_hashes()
root_after = blob.get_root_hash()

assert root_before != root_after, "root hash silently changed"
# The old leaf for KeyId(0) is now orphaned in the blob;
# get_proof_of_inclusion(KeyId(0)) returns a proof for the new leaf only,
# with a root hash that does not match root_before.
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L369-374)
```rust
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_insert.rs (L10-18)
```rust
    for (key, value, hash) in &args {
        match blob.insert(*key, *value, hash, InsertLocation::Auto {}) {
            Ok(_) => {}
            // should remain valid through these errors
            Err(Error::KeyAlreadyPresent()) => continue,
            Err(Error::HashAlreadyPresent()) => continue,
            // other errors should not be occurring
            Err(error) => panic!("unexpected error: {:?}", error),
        };
```
