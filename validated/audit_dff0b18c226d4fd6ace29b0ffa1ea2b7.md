### Title
`batch_insert` Bypasses Duplicate-Key Validation, Silently Corrupting DataLayer Merkle Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `insert` enforces. When the tree already holds ≥ 2 leaves, the entire batch bypasses those checks; when the tree holds ≤ 1 leaf, only the last two items (popped from the tail) pass through `insert`, while every earlier item in the vector is written directly to the blob without validation. Supplying a batch that contains a repeated `KeyId` or `Hash` silently inserts duplicate leaf nodes, producing an incorrect Merkle root and enabling forged proofs of inclusion against that root.

---

### Finding Description

**`insert` enforces uniqueness** at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

**`batch_insert` only calls `insert` for the first 1–2 items** (when `leaf_count <= 1`). All remaining items are written via `insert_entry_to_blob` directly, with no key or hash uniqueness check:

```rust
for ((key, value), hash) in keys_values_hashes {   // ← no duplicate check
    let new_leaf_index = self.get_new_index();
    ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

When the tree already has ≥ 2 leaves the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, so **every item** in the batch bypasses the guard. [3](#0-2) 

`insert_entry_to_blob` does call `block_status_cache.add_leaf`, but `add_leaf` uses `HashMap::insert`, which **silently overwrites** the existing cache entry for a duplicate key rather than returning an error:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silent overwrite
}
``` [4](#0-3) 

The result is a blob that contains **two physical leaf nodes** for the same key, while the cache tracks only the last one. The Merkle root is then computed over this structurally invalid tree.

`check_integrity` would catch the discrepancy (`leaf_count != key_to_index_cache_length`), but it is only called explicitly; in production the `check_integrity_on_drop` flag is `false`:

```rust
check_integrity_on_drop: cfg!(test),
``` [5](#0-4) 

The Python binding `py_batch_insert` exposes this path directly to callers: [6](#0-5) 

---

### Impact Explanation

The corrupted blob produces an incorrect Merkle root. Any `get_proof_of_inclusion` call on the tree thereafter generates a proof that is valid against the wrong root, satisfying the **High** impact criterion:

> *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

A relying party that trusts the root stored on-chain and verifies a proof against it will accept a proof for a key/value pair that was never legitimately committed, or for a value that has since been overwritten by the duplicate insertion. [7](#0-6) 

---

### Likelihood Explanation

Any caller of `batch_insert` (or its Python binding `py_batch_insert`) that supplies a vector containing a repeated `KeyId` triggers the bug. Because the DataLayer application uses `batch_insert` for bulk updates, a single malformed or adversarially crafted update payload is sufficient. No special privilege beyond the ability to submit a DataLayer update is required. The corruption is silent — no error is returned and no automatic integrity check runs.

---

### Recommendation

1. **Add pre-flight uniqueness checks inside `batch_insert`** before writing any item to the blob: iterate the incoming vector and verify that no `KeyId` or `Hash` appears twice in the batch, and that none already exists in `block_status_cache`.
2. Alternatively, route **all** items through `insert()` (accepting the performance trade-off), or build a temporary `HashSet` of keys/hashes from the batch and reject the call if any collision is detected.
3. Consider enabling `check_integrity_on_drop` (or an equivalent lightweight post-write assertion) in production builds to catch future regressions.

---

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
# (Hash is a 32-byte wrapper)

blob = MerkleBlob(bytearray())

# Bring the tree to ≥ 2 leaves so the entire batch bypasses insert()
blob.insert(KeyId(0), ValueId(0), bytes([0]*32))
blob.insert(KeyId(1), ValueId(1), bytes([1]*32))

# batch_insert with a duplicate KeyId — no error is raised
blob.batch_insert(
    [(KeyId(2), ValueId(2)), (KeyId(2), ValueId(99))],   # KeyId(2) appears twice
    [bytes([2]*32), bytes([3]*32)],
)

# The blob now contains two leaf nodes for KeyId(2).
# The Merkle root is computed over this invalid tree.
# check_integrity() will raise IntegrityKeyToIndexCacheLength,
# but it is never called automatically in production.
blob.check_integrity()   # → Error: leaf_count(4) != cache_length(3)
```

The duplicate leaf is physically present in the serialised blob, the root hash is wrong, and any proof of inclusion generated from this point forward is forged relative to the on-chain commitment.

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L325-331)
```rust
        let self_ = Self {
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1196)
```rust
    pub fn get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
        let mut index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;

        let node = self
            .get_node(index)?
            .expect_leaf("key to index mapping should only have leaves");

        let parents = self.get_lineage_blocks_with_indexes(index)?;
        let mut layers: Vec<proof_of_inclusion::ProofOfInclusionLayer> = Vec::new();
        let mut parents_iter = parents.iter();
        // first in the lineage is the index itself, second is the first parent
        parents_iter.next();
        for (next_index, block) in parents_iter {
            if block.metadata.dirty {
                return Err(Error::Dirty(*next_index));
            }
            let parent = block
                .node
                .expect_internal("all nodes after the first should be internal");
            let sibling_index = parent.sibling_index(index)?;
            let sibling_block = self.get_block(sibling_index)?;
            let sibling = sibling_block.node;
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
            layers.push(layer);
            index = *next_index;
        }

        Ok(proof_of_inclusion::ProofOfInclusion {
            node_hash: node.hash,
            layers,
        })
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
