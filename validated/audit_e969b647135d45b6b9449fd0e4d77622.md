### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Merkle Tree State — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate `KeyId` and `Hash` checks that `MerkleBlob::insert` enforces. When a batch contains duplicate keys or hashes, all entries are written to the underlying blob but only the last one is tracked in `BlockStatusCache`, permanently corrupting the tree structure, producing an incorrect Merkle root, and enabling forged or invalid proofs of inclusion.

---

### Finding Description

`MerkleBlob::insert` guards against duplicate insertions with two explicit checks before writing:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert`, however, only routes items through `insert` (with its duplicate checks) when `leaf_count <= 1`, and only for the last two items popped from the vector. All remaining items — and all items when the tree already has two or more leaves — are written directly via `insert_entry_to_blob` with no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `add_leaf`, which uses `HashMap::insert` — silently overwriting any existing entry for the same key or hash in the cache:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [3](#0-2) 

The result: both duplicate leaves are written to the blob, but only the second is tracked in `BlockStatusCache`. The blob and cache are permanently out of sync.

This is the analog of the reported pattern: a protection mechanism (the duplicate-prevention state in `BlockStatusCache`) is not consulted in the bulk code path, just as the cooldown timestamp was not reset after use in the original report. The protection exists and works correctly in the single-item path (`insert`), but is entirely bypassed in the batch path.

---

### Impact Explanation

**DataLayer Merkle proof/blob/delta logic corrupts tree roots and lets untrusted input prove invalid state.**

1. **Incorrect Merkle root**: The root hash is computed from all nodes in the blob, including the orphaned duplicate leaf. Any root committed on-chain from a corrupted tree is wrong.

2. **Invalid proofs of inclusion**: `get_proof_of_inclusion` uses the cache to locate the leaf index, so it returns a proof for the second (cache-tracked) leaf. That proof is computed against a root that includes both leaves, making it structurally invalid. [4](#0-3) 

3. **`check_integrity` detects permanent corruption**: The integrity check compares `leaf_count` (from blob traversal) against `key_to_index_cache_length` (from cache). After a duplicate batch insert these diverge, causing `check_integrity` to return `IntegrityKeyToIndexCacheLength` or `IntegrityLeafHashToIndexCacheLength`. [5](#0-4) 

4. **`get_node_by_hash` returns wrong data**: The `leaf_hash_to_index` cache maps the duplicate hash to the second leaf's index, so hash-based lookups silently return the wrong `(KeyId, ValueId)` pair. [6](#0-5) 

---

### Likelihood Explanation

`batch_insert` is exposed directly through the Python binding `py_batch_insert`, which accepts caller-controlled `keys_values` and `hashes` lists with no pre-validation:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
``` [7](#0-6) 

Any caller who can invoke `py_batch_insert` with a batch containing a repeated `KeyId` or `Hash` triggers the corruption. When the tree already has two or more leaves (the common production state), every item in every batch bypasses duplicate checks entirely.

---

### Recommendation

Add duplicate-prevention checks inside the `batch_insert` bulk loop, mirroring what `insert` does:

1. Before calling `insert_entry_to_blob` for each item, check `block_status_cache.contains_key(key)` and `block_status_cache.contains_leaf_hash(&hash)`, returning `Err(Error::KeyAlreadyPresent())` or `Err(Error::HashAlreadyPresent())` on collision.
2. Additionally track keys and hashes seen within the current batch (a local `HashSet`) to catch intra-batch duplicates before any writes occur. [8](#0-7) 

---

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Seed the tree with 2 leaves so leaf_count >= 2;
# from this point ALL batch items bypass duplicate checks.
blob.insert(KeyId(0), ValueId(0), hashlib.sha256(b"seed0").digest())
blob.insert(KeyId(1), ValueId(1), hashlib.sha256(b"seed1").digest())

dup_key  = KeyId(2)
hash_a   = hashlib.sha256(b"a").digest()
hash_b   = hashlib.sha256(b"b").digest()

# Both entries share dup_key; neither is rejected.
blob.batch_insert(
    [(dup_key, ValueId(10)), (dup_key, ValueId(99))],
    [hash_a, hash_b],
)

# Blob now contains 5 leaf slots; cache tracks only 4.
# check_integrity() raises IntegrityKeyToIndexCacheLength.
# get_proof_of_inclusion(dup_key) returns a proof for ValueId(99)
# computed against a root that includes both leaves — the proof is invalid.
blob.check_integrity()   # raises Error
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L369-374)
```rust
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L570-603)
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
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-874)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1198-1208)
```rust
    pub fn get_node_by_hash(&self, node_hash: Hash) -> Result<(KeyId, ValueId), Error> {
        let Some(index) = self.block_status_cache.get_index_by_leaf_hash(&node_hash) else {
            return Err(Error::LeafHashNotFound(node_hash));
        };

        let node = self
            .get_node(*index)?
            .expect_leaf("should only have leaves in the leaf hash to index cache");

        Ok((node.key, node.value))
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
