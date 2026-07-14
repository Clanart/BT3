### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key Validation, Corrupting DataLayer Merkle Tree Root and Invalidating Proofs of Inclusion - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert()` contains a fast-path code branch that writes leaf nodes directly to the blob via `insert_entry_to_blob()` without performing the duplicate key or duplicate hash checks that `insert()` enforces. When a caller supplies a batch containing duplicate `KeyId` values (or duplicate leaf hashes), the fast path silently inserts both entries into the blob, leaving the `BlockStatusCache` in an inconsistent state where the `key_to_index` map only retains the last index for the duplicated key. The resulting Merkle tree has two leaf nodes for the same key, producing a corrupted root hash and invalidating all proofs of inclusion derived from it.

### Finding Description

**Root cause — missing validation in the fast path of `batch_insert`**

`MerkleBlob::insert()` enforces two invariants before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert()` uses `insert()` only for the last two items when the tree has ≤ 1 existing leaf. All other items are written through the fast path:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 578-603
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← has duplicate checks
    }
}

for ((key, value), hash) in keys_values_hashes {          // ← NO duplicate checks here
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // ← writes directly
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob()` calls `block_status_cache.add_leaf()`, which performs a plain `HashMap::insert` on `key_to_index` and `leaf_hash_to_index`:

```rust
// lines 1024-1027
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    ...
}
``` [3](#0-2) 

```rust
// lines 188-192
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silently overwrites
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silently overwrites
}
``` [4](#0-3) 

**Resulting state after a duplicate-key batch**

Suppose key `K` appears at positions `i` and `j` in the batch (both going through the fast path). After the call:
- The blob contains two leaf nodes with key `K` at tree indexes `I1` and `I2`.
- `key_to_index[K]` = `I2` (the second write silently overwrote `I1`).
- The tree structure references both `I1` and `I2` as live leaves.

`check_integrity()` will detect the inconsistency:

```rust
// lines 841-851
let cached_index = self.block_status_cache
    .get_index_by_key(node.key)
    .ok_or(Error::IntegrityKeyNotInCache(node.key))?;
if *cached_index != index {
    return Err(Error::IntegrityKeyToIndexCacheIndex(node.key, index, *cached_index));
}
``` [5](#0-4) 

But `batch_insert` itself returns `Ok(())` with no error, so the corruption is silent at the call site.

**Exposed Python binding**

The function is directly callable from Python:

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
``` [6](#0-5) 

The Python stub confirms it is a public API: `def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...` [7](#0-6) 

### Impact Explanation

The DataLayer Merkle tree is the authoritative data structure for proving inclusion and exclusion of key-value pairs. Corrupting it by inserting duplicate keys causes:

1. **Corrupted root hash** — the root hash is computed over a tree that contains two leaf nodes for the same key. Any root hash committed to the chain from this state is invalid.
2. **Forged or invalid proofs of inclusion** — `get_proof_of_inclusion(K)` uses `key_to_index[K]` (pointing to `I2`) to build the proof, but the tree structure also contains `I1`. The proof path is inconsistent with the actual tree, so `proof.valid()` may return `false` for legitimately inserted keys, or a proof may be generated for the wrong leaf.
3. **Persistent store corruption** — the corrupted blob can be serialized to disk via `to_path()`, making the corruption durable.

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

### Likelihood Explanation

The trigger condition is straightforward: call `batch_insert` with a list containing a repeated `KeyId`. This requires no privileged access — the Python binding is public. Any DataLayer application that accepts external key-value data and passes it to `batch_insert` without prior deduplication is vulnerable. The fast path is taken for all items when the tree already has ≥ 2 leaves (the common production case), so the bug is reachable on every non-trivial store.

### Recommendation

Add duplicate-key and duplicate-hash checks at the start of the fast-path loop in `batch_insert`, mirroring the checks in `insert()`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    let new_leaf_index = self.get_new_index();
    ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

Alternatively, deduplicate the input vector before processing, or add a pre-pass that validates uniqueness of all keys and hashes in the batch.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
from hashlib import sha256

def make_hash(n: int):
    return sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 leaves so the fast path is always taken
blob.insert(KeyId(100), ValueId(100), make_hash(100))
blob.insert(KeyId(101), ValueId(101), make_hash(101))

# Now batch_insert with a duplicate key (KeyId(1) appears twice)
keys_values = [(KeyId(1), ValueId(1)), (KeyId(2), ValueId(2)), (KeyId(1), ValueId(99))]
hashes      = [make_hash(1),           make_hash(2),           make_hash(999)]

# Returns Ok(()) — no error raised
blob.batch_insert(keys_values, hashes)

# Tree is now corrupted: two leaf nodes with KeyId(1) exist in the blob,
# but the cache only knows about the second one.
# check_integrity() will raise IntegrityKeyToIndexCacheIndex.
blob.check_integrity()   # raises Error
```

The blob's root hash after this call is computed over a structurally invalid tree. Any proof of inclusion for `KeyId(1)` generated from this state will be inconsistent with the actual blob contents.

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L841-851)
```rust
                    let cached_index = self
                        .block_status_cache
                        .get_index_by_key(node.key)
                        .ok_or(Error::IntegrityKeyNotInCache(node.key))?;
                    if *cached_index != index {
                        return Err(Error::IntegrityKeyToIndexCacheIndex(
                            node.key,
                            index,
                            *cached_index,
                        ));
                    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
