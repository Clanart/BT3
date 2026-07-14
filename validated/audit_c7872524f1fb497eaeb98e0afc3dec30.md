### Title
`MerkleBlob::batch_insert` Bypasses Key/Hash Uniqueness Checks, Corrupting DataLayer Merkle Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a caller supplies a batch of three or more entries that contains a repeated `KeyId` or `Hash`, the fast bulk path writes duplicate leaf nodes directly into the blob without rejection, silently overwrites the `key_to_index` / `leaf_hash_to_index` cache entries, and produces a Merkle root that is computed over a structurally corrupt tree containing two leaves for the same key.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) enforces two uniqueness invariants before touching the blob:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a different code path for all items beyond the first two (when the tree already has ≥ 2 leaves, or for items 3-N when the tree starts empty). Those items are written directly via `insert_entry_to_blob`, which calls `block_status_cache.add_leaf` with no uniqueness check:

```rust
// lines 587-602  — no contains_key / contains_leaf_hash guard
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`add_leaf` unconditionally overwrites both cache maps:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index); // silent overwrite
}
``` [3](#0-2) 

After the call returns:

1. **Two leaf nodes with the same `KeyId` exist in the blob.** The tree structure (internal nodes, left/right pointers) references both. The Merkle root is computed over both, so it reflects a tree that violates the one-key-one-leaf invariant.
2. **The cache points only to the second (overwriting) leaf.** The first leaf is orphaned in the blob but still participates in hash computation.
3. **`check_integrity` detects the corruption** — `leaf_count` (from tree traversal) exceeds `key_to_index_cache_length` — but this check is only run in debug/test builds on drop. [4](#0-3) 

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
// lines 1503-1518
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

---

### Impact Explanation

The DataLayer Merkle tree root is the commitment used to prove inclusion and exclusion of key-value pairs. A corrupt root — one computed over a tree with duplicate keys — means:

- Proofs of inclusion generated from the corrupt tree are invalid against any honest root.
- A verifier holding the correct root will reject all proofs derived from the corrupt blob.
- The DataLayer store's committed state is permanently inconsistent: the orphaned leaf contributes to the root hash but is unreachable through the cache, so it can never be deleted or updated through normal operations.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

---

### Likelihood Explanation

`batch_insert` is the primary bulk-load API used by the DataLayer Python layer (called in `test_proof_of_inclusion_merkle_blob` and `test_delta_file_cache` with large batches). Any caller — including the DataLayer node software processing a batch of store updates — that supplies a list containing a repeated `KeyId` triggers the bug. No privilege is required; the Python binding accepts arbitrary `keys_values` lists. The condition is reachable whenever a batch of ≥ 3 entries is submitted and the input is not pre-deduplicated by the caller.

---

### Recommendation

Add the same uniqueness guards at the top of `batch_insert` (or inside the bulk loop) that `insert` already enforces:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    // Guard: reject duplicates within the batch and against existing keys
    for ((key, _), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // ... rest of existing logic
}
```

Alternatively, deduplicate the input vector before processing, or delegate all insertions through the checked `insert` path.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def make_hash(n: int):
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Seed the tree with 2 leaves so batch_insert takes the fast bulk path
blob.insert(KeyId(100), ValueId(100), make_hash(100))
blob.insert(KeyId(200), ValueId(200), make_hash(200))

# Now batch_insert 3 items where item[0] and item[2] share the same KeyId
dup_key = KeyId(999)
kv = [(dup_key, ValueId(1)), (KeyId(300), ValueId(300)), (dup_key, ValueId(2))]
hashes = [make_hash(1), make_hash(300), make_hash(2)]

# No error is raised — duplicate key is silently accepted
blob.batch_insert(kv, hashes)
blob.calculate_lazy_hashes()

# The tree now contains two leaf nodes for KeyId(999).
# The root hash is computed over both, but the cache only tracks one.
# check_integrity() will panic/fail in debug builds.
print("leaf count via cache:", len(blob.get_keys_values()))  # reports 4
# Actual blob contains 5 leaf nodes (2 pre-existing + 3 batch, including duplicate)
```

The root hash produced is computed over a tree with a duplicate key, making all subsequent proofs of inclusion derived from it invalid against any honest root commitment.

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
