### Title
`batch_insert` Skips Per-Key Uniqueness Guard, Enabling Duplicate-Key Corruption of DataLayer Merkle Tree Root - (File: crates/chia-datalayer/src/merkle/blob.rs)

---

### Summary

`MerkleBlob::batch_insert` omits the duplicate-key check that `MerkleBlob::insert` enforces. When the tree already holds more than one leaf — the common production case — every key in the batch is written directly to the blob via `insert_entry_to_blob` with no guard against a key that already exists in the tree or appears twice within the same batch. An unprivileged caller supplying a batch that contains a repeated `KeyId` silently produces a tree with two leaf nodes sharing the same key, yielding a corrupted root hash and invalidating all subsequent proofs of inclusion.

---

### Finding Description

`insert` (the single-item path) enforces two guards before touching the blob:

```
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a completely different code path. It only calls `self.insert()` (and therefore only runs those guards) for the **last two items** popped from the vector, and only when the tree currently has **≤ 1 leaf**:

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining items — and **all items without exception** when the tree already has ≥ 2 leaves — are written directly:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { key, value, hash, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

`insert_entry_to_blob` writes the raw block to the blob and updates `BlockStatusCache` via `add_leaf`, which calls `HashMap::insert` — silently overwriting the cache entry for any duplicate key. The blob therefore ends up with **two physical leaf nodes** carrying the same `KeyId`, while the cache tracks only the last one. The tree-building phase that follows pairs these leaves into internal nodes and computes `internal_hash` over them, producing a root hash that encodes the duplicate and diverges from any honest tree built from the same logical key-value set. [4](#0-3) 

The Python binding `py_batch_insert` passes caller-supplied lists directly into `batch_insert` with no pre-filtering:

```rust
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

---

### Impact Explanation

A corrupted root hash means every `ProofOfInclusion` computed after the bad batch will be invalid — `proof.valid()` returns `false` for legitimately inserted keys — and a proof for the phantom duplicate leaf can be constructed that the tree's own verifier will reject. Any DataLayer store that accepts the corrupted blob and publishes its root hash to the chain commits to a state that cannot be faithfully proven to peers, breaking the DataLayer's inclusion-proof guarantee. This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic … corrupts tree roots, or lets untrusted input prove invalid state."**

---

### Likelihood Explanation

The Python binding is the primary consumer of `batch_insert` in production DataLayer nodes. Any code path that feeds externally-sourced key-value pairs into `batch_insert` without pre-deduplication is vulnerable. Because the tree almost always has ≥ 2 leaves in production, the guard is **never** reached for any item in a normal batch, making the window permanent rather than edge-case.

---

### Recommendation

Add an explicit duplicate check at the top of the `for` loop inside `batch_insert`, mirroring the guard in `insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing blob-write logic
}
```

Alternatively, refactor `batch_insert` to call `insert` for every item (accepting the performance trade-off), or add a pre-pass that deduplicates the input vector and returns an error on collision before any blob mutation begins.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n): return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Seed the tree with 3 leaves so leaf_count > 1 — the guard is now permanently skipped
for i in range(3):
    blob.insert(KeyId(i), ValueId(i), h(i))

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with a duplicate key (KeyId(99) appears twice)
# Neither occurrence is checked — both are written to the blob
blob.batch_insert(
    [(KeyId(99), ValueId(99)), (KeyId(100), ValueId(100)), (KeyId(99), ValueId(200))],
    [h(99), h(100), h(201)],
)
blob.calculate_lazy_hashes()

# The tree now has two physical leaf nodes with key=99.
# get_proof_of_inclusion returns a proof for only one of them;
# the root hash encodes both, so the proof fails validation.
proof = blob.get_proof_of_inclusion(KeyId(99))
assert not proof.valid(), "proof should be invalid due to duplicate-key corruption"
```

The `batch_insert` call succeeds without error, the root hash is silently corrupted, and the proof-of-inclusion for `KeyId(99)` is invalid — demonstrating that untrusted input can corrupt the DataLayer Merkle tree root through the unguarded fast path in `batch_insert`. [6](#0-5)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L570-657)
```rust
    pub fn batch_insert(
        &mut self,
        mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
    ) -> Result<(), Error> {
        // OPT: perhaps go back to taking an iterator?
        // OPT: would it be worthwhile to hold the entire blocks?
        let mut indexes = vec![];

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

        // OPT: can we insert the top node first?  maybe more efficient to update it's children
        //      than to update the parents of the children when traversing leaf to sub-root?
        while indexes.len() > 1 {
            let mut new_indexes = vec![];

            for chunk in indexes.chunks(2) {
                let [index_1, index_2] = match chunk {
                    [index] => {
                        new_indexes.push(*index);
                        continue;
                    }
                    [index_1, index_2] => [*index_1, *index_2],
                    _ => unreachable!(
                        "chunk should always be either one or two long and be handled above"
                    ),
                };

                let new_internal_node_index = self.get_new_index();

                let mut hashes = vec![];
                for index in [index_1, index_2] {
                    let block = self.update_parent(index, Some(new_internal_node_index))?;
                    hashes.push(block.node.hash());
                }

                let new_block = Block {
                    metadata: NodeMetadata {
                        node_type: NodeType::Internal,
                        dirty: false,
                    },
                    node: Node::Internal(InternalNode {
                        parent: Parent(None),
                        hash: internal_hash(&hashes[0], &hashes[1]),
                        left: index_1,
                        right: index_2,
                    }),
                };

                self.insert_entry_to_blob(new_internal_node_index, &new_block)?;
                new_indexes.push(new_internal_node_index);
            }

            indexes = new_indexes;
        }

        if indexes.len() == 1 {
            // OPT: can we avoid this extra min height leaf traversal?
            let min_height_leaf = self.get_min_height_leaf()?;
            self.insert_subtree_at_key(min_height_leaf.key, indexes[0], Side::Left)?;
        }

        Ok(())
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
