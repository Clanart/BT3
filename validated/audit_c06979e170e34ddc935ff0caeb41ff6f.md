### Title
`MerkleBlob::batch_insert` Skips Uniqueness Validation for Duplicate Keys, Enabling Merkle Tree Root Corruption — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that the single-item `insert` path enforces. When the tree already has more than one leaf (`leaf_count() > 1`), every item in the batch is written directly to the blob without any uniqueness check. Supplying a batch that contains duplicate `KeyId` values — or keys already present in the tree — silently inserts multiple leaf nodes with the same key, corrupting the Merkle tree structure and producing an incorrect root hash. Proof-of-inclusion results derived from the corrupted tree are unreliable.

### Finding Description

`MerkleBlob::insert` (the single-item path) enforces two guards before writing:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a different code path. When `leaf_count() > 1` the early-exit branch that calls `self.insert()` is skipped entirely, and every item in the batch is written via `insert_entry_to_blob` — a low-level blob writer that performs no key-uniqueness or hash-uniqueness check:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 570-603
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    let mut indexes = vec![];

    if self.block_status_cache.leaf_count() <= 1 {   // ← guard only for empty/single-leaf trees
        for _ in 0..2 {
            let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                return Ok(());
            };
            self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← safe path
        }
    }

    for ((key, value), hash) in keys_values_hashes {   // ← ALL items when leaf_count > 1
        let new_leaf_index = self.get_new_index();
        let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
        self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // ← no duplicate check
        indexes.push(new_leaf_index);
    }
    ...
``` [2](#0-1) 

Even when `leaf_count() <= 1`, only the last two items (popped from the vector) go through the safe path; all earlier items in the vector still bypass the check.

The Python binding `py_batch_insert` exposes this directly to callers:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 1503-1518
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
``` [3](#0-2) 

### Impact Explanation

Inserting duplicate leaf nodes corrupts the internal tree structure: the `block_status_cache` is not updated for the directly-written leaves, so the cache and the blob diverge. The Merkle root computed from the blob will be incorrect. Any `get_proof_of_inclusion` call on a key that has a duplicate leaf will traverse the wrong tree path, producing a proof that either falsely validates or falsely rejects inclusion. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state**.

### Likelihood Explanation

The DataLayer node calls `batch_insert` when syncing data from peers. If peer-supplied key-value batches are not deduplicated before being passed to `batch_insert`, a malicious peer can craft a batch containing repeated `KeyId` values. Once the tree has more than one leaf (the common steady-state), every subsequent `batch_insert` call is unprotected. The Python binding makes this reachable from the DataLayer application layer without any Rust-level guard.

### Recommendation

Add uniqueness validation inside `batch_insert` before writing items to the blob:

1. Check each incoming `KeyId` against `self.block_status_cache` (same as `insert` does).
2. Check each incoming `Hash` against `self.block_status_cache`.
3. Detect duplicates within the batch itself (e.g., collect into a temporary `HashSet` before writing).

Return `Error::KeyAlreadyPresent()` or `Error::HashAlreadyPresent()` on violation, consistent with the single-item `insert` contract.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def make_hash(n):
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(bytearray())

# Seed the tree with 3 leaves so leaf_count > 1
for i in range(3):
    blob.insert(KeyId(i), ValueId(i), make_hash(i))

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with a duplicate key (key=0 already exists)
# When leaf_count > 1, no duplicate check is performed
blob.batch_insert(
    [(KeyId(0), ValueId(99))],   # duplicate of existing key 0
    [make_hash(99)],
)
blob.calculate_lazy_hashes()
root_after = blob.get_root_hash()

# root_after is now incorrect; the tree has two leaves with KeyId(0)
assert root_before != root_after, "Root changed due to duplicate insertion"

# Proof of inclusion for key 0 now traverses a corrupted tree
proof = blob.get_proof_of_inclusion(KeyId(0))
# proof.valid() may return True or False inconsistently
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1195)
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
