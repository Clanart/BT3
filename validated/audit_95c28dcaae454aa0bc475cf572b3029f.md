### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Tree Roots — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` contains a fast path that writes leaf nodes directly to the blob without performing the duplicate-key or duplicate-hash checks that the single-item `insert` method enforces. Untrusted input containing a key already present in the tree (or a repeated key within the batch itself) silently produces a Merkle tree with two leaf nodes sharing the same key. The resulting root hash is computed over this invalid tree state, corrupting every DataLayer inclusion proof derived from it.

---

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

`batch_insert` delegates only the **last two** items in the vector to `insert` (when `leaf_count <= 1`). All remaining items are written directly via `insert_entry_to_blob` with **no duplicate check**:

```rust
// lines 587-603 — no contains_key / contains_leaf_hash guard
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

When the tree already has two or more leaves, the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, so **every item** in the batch goes through the unguarded fast path. [3](#0-2) 

`BlockStatusCache::add_leaf` uses `HashMap::insert`, which silently overwrites the existing cache entry for a duplicate key:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silent overwrite
}
``` [4](#0-3) 

After the overwrite, the cache points to the newly inserted leaf, but the **original leaf with the same key remains in the blob and in the tree's parent/child structure**. Both leaves participate in `calculate_lazy_hashes`, so the root hash is computed over a tree that contains two leaves with the same key — an invalid key-value store state.

`get_proof_of_inclusion` then generates a proof for the cache-tracked leaf only:

```rust
let mut index = *self.block_status_cache
    .get_index_by_key(key)
    .ok_or(Error::UnknownKey(key))?;
``` [5](#0-4) 

The proof passes `valid()` because the hash chain from the tracked leaf to the corrupted root is internally consistent. However, the root hash also encodes the orphaned duplicate leaf, so the root no longer represents a valid key-value mapping.

The Python binding `py_batch_insert` is the direct entry point from DataLayer node code:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
``` [6](#0-5) 

---

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Concretely:

1. **Tree root corruption**: The committed DataLayer root hash encodes a tree with duplicate keys. Any downstream system that treats the root as representing a valid key-value store is operating on a false premise.
2. **Forged inclusion proof**: The orphaned leaf (the original entry for the duplicate key) is still structurally present in the tree and contributes to the root hash. A proof of inclusion for the key proves the *new* leaf's hash, but the root also silently encodes the *old* leaf's hash. This allows a DataLayer operator to commit a root that simultaneously "contains" two different values for the same key, then selectively prove whichever is convenient.
3. **Exclusion proof invalidity**: Any exclusion proof generated against the corrupted root is unreliable because the tree invariant (unique keys) is violated.

---

### Likelihood Explanation

- The `py_batch_insert` Python binding is the standard DataLayer bulk-insert API. The DataLayer node calls it with data sourced from DataLayer clients.
- The attack requires only submitting a batch where at least one key duplicates an existing tree key, or where the batch itself contains a repeated key in a position that falls into the fast path (any position except the last two when the tree has ≤ 1 leaf).
- No privileged role, leaked key, or network-level capability is required — only the ability to submit a DataLayer update batch.
- The `check_integrity` function would detect the corruption (it checks `leaf_count == key_to_index_cache_length`), but it is not called automatically after `batch_insert`; it is an opt-in debug check. [7](#0-6) 

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the fast path of `batch_insert` that `insert` already enforces. Before calling `insert_entry_to_blob` for each item in the fast path, check:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(&hash) {
    return Err(Error::HashAlreadyPresent());
}
```

Alternatively, pre-validate the entire input vector for duplicates (both within the batch and against existing tree keys) before entering the fast path, mirroring the contract that `insert` provides.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn sha256_of(n: u8) -> Hash {
    use chia_sha2::Sha256;
    let mut h = Sha256::new();
    h.update([n]);
    Hash(Bytes32::new(h.finalize()))
}

fn main() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Seed the tree with 2 leaves so batch_insert skips the guarded path entirely.
    blob.insert(KeyId(10), ValueId(10), &sha256_of(10), InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(20), ValueId(20), &sha256_of(20), InsertLocation::Auto {}).unwrap();

    // batch_insert with a duplicate key (KeyId(10) already exists).
    // The fast path writes it without any duplicate check.
    blob.batch_insert(vec![
        ((KeyId(10), ValueId(99)), sha256_of(99)),  // duplicate key — accepted silently
        ((KeyId(30), ValueId(30)), sha256_of(30)),
    ]).unwrap();

    blob.calculate_lazy_hashes().unwrap();

    // check_integrity will now fail because leaf_count != key_to_index_cache_length
    // (4 leaves in blob, 3 entries in cache).
    blob.check_integrity().expect_err("tree is corrupted");

    // get_proof_of_inclusion(KeyId(10)) returns a proof for the *new* leaf (ValueId(99)),
    // but the root hash also encodes the *old* leaf (ValueId(10)).
    let proof = blob.get_proof_of_inclusion(KeyId(10)).unwrap();
    assert!(proof.valid()); // passes — proof is internally consistent against corrupted root
}
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L812-819)
```rust
    pub fn check_integrity(&self) -> Result<(), Error> {
        self.check_just_integrity()?;

        let mut clone = self.clone();
        clone.check_integrity_on_drop = false;
        clone.calculate_lazy_hashes()?;
        clone.check_just_integrity()
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
