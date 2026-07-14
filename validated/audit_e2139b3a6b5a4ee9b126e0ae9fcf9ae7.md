### Title
`MerkleBlob::batch_insert` Silently Accepts Duplicate Keys, Corrupting the Merkle Tree Root and Proof State - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key guard for all items beyond the first two (or for every item when the tree already has ≥ 2 leaves). Duplicate `KeyId` values are written directly into the blob and silently overwrite the `BlockStatusCache`, producing a tree whose root hash is computed over phantom leaf nodes and whose proof-of-inclusion/exclusion answers are wrong.

### Finding Description

`MerkleBlob::insert` enforces two invariants before writing a leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` (with those guards) only for the last two items popped from the input vector when the tree has ≤ 1 existing leaves. Every other item is written through the unchecked fast-path:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no duplicate check
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which unconditionally overwrites the `key_to_index` and `leaf_hash_to_index` maps:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silent overwrite
}
``` [3](#0-2) 

When the tree already has ≥ 2 leaves the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, so **every** item in the batch goes through the unchecked path. [4](#0-3) 

The Python binding `py_batch_insert` passes caller-supplied `(KeyId, ValueId, Hash)` triples directly to `batch_insert` with no additional validation:

```rust
// lines 1503-1518
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

### Impact Explanation

After a batch with a repeated `KeyId` K is processed:

1. **Two leaf nodes with key K exist in the blob** at different `TreeIndex` positions.
2. **`key_to_index[K]` points only to the last-written index.** The earlier leaf is an orphan — present in the serialised blob, counted in the tree structure, but unreachable through the cache.
3. **The Merkle root is computed over the full tree including the orphan**, so the committed root hash does not correspond to any valid key-value mapping.
4. **`get_proof_of_inclusion(K)`** returns a proof for the last-written leaf only; the orphan leaf can never be proven or disproven.
5. **`check_integrity`** will detect the inconsistency (`IntegrityKeyToIndexCacheLength` or `IntegrityLeafHashToIndexCacheLength`) only if explicitly called — it is not called automatically after `batch_insert`.

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.* [6](#0-5) 

### Likelihood Explanation

The Python binding is the primary public API for DataLayer operations. Any caller that supplies a batch containing a key already present in the tree, or a batch with an internally repeated key, triggers the corruption silently — no error is returned. The DataLayer software does not appear to deduplicate keys before calling `batch_insert`. [5](#0-4) 

### Recommendation

1. **Add a duplicate-key check inside the fast-path loop in `batch_insert`**, mirroring the guard in `insert`:
   ```rust
   if self.block_status_cache.contains_key(key) {
       return Err(Error::KeyAlreadyPresent());
   }
   if self.block_status_cache.contains_leaf_hash(&hash) {
       return Err(Error::HashAlreadyPresent());
   }
   ```
2. **Alternatively, refactor `batch_insert`** so that all items — regardless of the current leaf count — are validated through `insert` or a shared validation helper before being written to the blob.
3. **Add a test** that passes a batch containing a key already present in the tree (with ≥ 2 existing leaves) and asserts `KeyAlreadyPresent` is returned. [7](#0-6) 

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId, Hash
import hashlib

def h(n): return Hash(hashlib.sha256(n.to_bytes(8,'big')).digest())

blob = MerkleBlob(blob=bytearray())

# Insert 3 leaves so the tree has >= 2 leaves before the batch
for i in range(3):
    blob.insert(KeyId(i), ValueId(i), h(i))

# batch_insert with a key (KeyId(0)) already present in the tree.
# With >= 2 existing leaves the entire batch bypasses the duplicate check.
blob.batch_insert(
    [(KeyId(0), ValueId(99)), (KeyId(10), ValueId(10)), (KeyId(11), ValueId(11))],
    [h(100), h(10), h(11)]
)
# No error raised — KeyId(0) is now duplicated in the blob.
# The root hash is computed over a tree with two leaves for key 0.
# check_integrity() will now fail or report inconsistency.
blob.check_integrity()  # raises or silently passes with corrupted state
``` [8](#0-7)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L821-887)
```rust
    fn check_just_integrity(&self) -> Result<(), Error> {
        let mut leaf_count: usize = 0;
        let mut internal_count: usize = 0;
        let mut child_to_parent: HashMap<TreeIndex, TreeIndex> = HashMap::new();

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (index, block) = item?;
            if let Some(parent) = block.node.parent().0 {
                if child_to_parent.remove(&index) != Some(parent) {
                    return Err(Error::IntegrityParentChildMismatch(index));
                }
            }
            match block.node {
                Node::Internal(node) => {
                    internal_count += 1;
                    child_to_parent.insert(node.left, index);
                    child_to_parent.insert(node.right, index);
                }
                Node::Leaf(node) => {
                    leaf_count += 1;
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
                    assert!(
                        !self.block_status_cache.is_index_free(index),
                        "{}",
                        format!("active index found in free index list: {index:?}")
                    );
                }
            }
        }

        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
        let total_count = leaf_count + internal_count + self.block_status_cache.free_index_count();
        let extend_index = self.extend_index();
        if total_count != extend_index.0 as usize {
            return Err(Error::IntegrityTotalNodeCount(extend_index, total_count));
        }
        if !child_to_parent.is_empty() {
            return Err(Error::IntegrityUnmatchedChildParentRelationships(
                child_to_parent.len(),
            ));
        }

        Ok(())
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
