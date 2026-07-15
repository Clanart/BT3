### Title
`MerkleBlob::batch_insert` Skips Duplicate-Key/Hash Validation, Corrupting Merkle Tree Root and Invalidating Proofs of Inclusion - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a batch containing a repeated `KeyId` or `Hash` is supplied (with three or more total entries and a pre-existing tree of ≥ 2 leaves), duplicate leaf nodes are written directly into the blob. The `block_status_cache` silently overwrites the key→index mapping for the duplicate, leaving an orphaned leaf permanently embedded in the tree. The resulting Merkle root commits to unreachable data, proofs of inclusion become inconsistent with the root, and the orphaned leaf can never be deleted.

---

### Finding Description

`MerkleBlob::insert` guards against duplicates at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` only for the first two items when the tree has ≤ 1 existing leaf. For all remaining items it calls `insert_entry_to_blob` directly, with **no duplicate check**:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` writes the block to the blob and calls `block_status_cache.add_leaf`, which inserts into the `key_to_index` and `leaf_hash_to_index` HashMaps:

```rust
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    Node::Internal(..) => self.block_status_cache.add_internal(index),
}
``` [3](#0-2) 

When a duplicate `KeyId` appears in the batch, `add_leaf` silently overwrites the `key_to_index` entry, pointing the key to the new index. The first leaf node remains in the blob at its original index, is wired into the tree structure by the subsequent subtree-linking phase, and is committed into the Merkle root — but is no longer reachable through any cache lookup. It can never be deleted because `delete` resolves the leaf through the cache.

The Python binding `py_batch_insert` exposes this path directly:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [4](#0-3) 

The Python stub confirms `batch_insert` is part of the public `MerkleBlob` API: [5](#0-4) 

`check_integrity` would detect the corruption (leaf count in blob ≠ cache length), but it is not called automatically after `batch_insert`: [6](#0-5) 

---

### Impact Explanation

**Corrupted Merkle root.** The root hash is computed over a tree that contains one or more orphaned leaf nodes (duplicate keys). The root no longer faithfully represents the set of key→value mappings tracked by the cache.

**Invalid proofs of inclusion.** `get_proof_of_inclusion` resolves the leaf through the cache (pointing to the last-written duplicate). The proof path it constructs is anchored to that leaf's position, but the root hash was computed over a tree that also includes the orphaned sibling leaf. Depending on tree topology, the proof may fail to verify against the root, or the root itself misrepresents the committed state. [7](#0-6) 

**Permanent orphan / memory leak.** The orphaned leaf is embedded in the tree structure and cannot be removed: `delete` uses the cache to locate the leaf, and the cache no longer holds the orphan's index.

**Allowed impact category matched:** *High — DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.*

---

### Likelihood Explanation

`batch_insert` is the primary bulk-insertion API for the DataLayer and is called with caller-supplied lists of `(KeyId, ValueId, Hash)` tuples. Any caller — including via the Python binding — that passes a list containing a repeated `KeyId` (e.g., two updates to the same key in one batch, or a malformed DataLayer delta) triggers the corruption silently, with no error returned. The DataLayer application layer is responsible for deduplicating inputs before calling `batch_insert`, but the library itself provides no safety net, unlike `insert`.

---

### Recommendation

Add duplicate-key and duplicate-hash validation at the start of the bulk-insertion loop in `batch_insert`, mirroring the guards in `insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insertion logic
}
```

Alternatively, pre-validate the entire input batch for uniqueness before any blob writes begin, so that the tree is never left in a partially-written corrupt state on error.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn main() {
    // Pre-populate with 2 leaves so batch_insert takes the fast path
    // (skipping the first-two-via-insert branch)
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    blob.insert(KeyId(100), ValueId(100), &Hash(Bytes32::new([0xaa; 32])), InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(101), ValueId(101), &Hash(Bytes32::new([0xbb; 32])), InsertLocation::Auto {}).unwrap();

    // Batch with a duplicate KeyId(1) — no error is returned
    let batch = vec![
        ((KeyId(1), ValueId(10)), Hash(Bytes32::new([0x01; 32]))),
        ((KeyId(2), ValueId(20)), Hash(Bytes32::new([0x02; 32]))),
        ((KeyId(1), ValueId(99)), Hash(Bytes32::new([0x03; 32]))), // duplicate key!
    ];
    blob.batch_insert(batch).unwrap(); // succeeds silently

    blob.calculate_lazy_hashes().unwrap();

    // check_integrity now fails: blob has 5 leaves, cache has 4 keys
    blob.check_integrity().unwrap_err(); // IntegrityKeyToIndexCacheLength

    // The root hash commits to 5 leaves, but only 4 are reachable
    // get_proof_of_inclusion(KeyId(1)) returns a proof anchored to the
    // last-written duplicate, while the root includes the orphaned first copy
}
``` [8](#0-7) [1](#0-0)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
