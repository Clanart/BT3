Now I have all the information needed. Let me trace through the exact code path.

The code path is fully traceable. Here is the complete analysis:

---

### Title
Missing Hash-Collision Guard in `MerkleBlob::upsert` Corrupts `leaf_hash_to_index` and Tree Root — (`crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::upsert` removes the old leaf hash from `leaf_hash_to_index` then unconditionally inserts the new hash via `add_leaf`, which calls `HashMap::insert` and silently overwrites any pre-existing entry for that hash. The sibling function `insert` explicitly guards against this with `contains_leaf_hash`, but `upsert` has no equivalent guard. Calling `upsert(key_A, new_val, hash_B)` — where `hash_B` is already the stored hash of a different leaf `key_B` — overwrites `leaf_hash_to_index[hash_B]` from `leaf_B_index` to `leaf_A_index`, leaving two live leaves sharing the same hash while the cache tracks only one of them.

### Finding Description

**`insert` guard (present):** [1](#0-0) 

**`upsert` — no equivalent guard:** [2](#0-1) 

Step-by-step through `upsert(key_A, new_val, hash_B)` when `key_A` already exists:

1. `remove_leaf(&leaf_A)` — removes `hash_A` from `leaf_hash_to_index`, removes `key_A` from `key_to_index`, adds `leaf_A_index` to `free_indexes`. [3](#0-2) 

2. `leaf.hash = hash_B` — the leaf's hash field is overwritten with the attacker-supplied value. [4](#0-3) 

3. `insert_entry_to_blob(leaf_A_index, &block)` — writes the updated block to the blob, then calls `add_leaf(leaf_A_index, leaf_with_hash_B)`. [5](#0-4) 

4. `add_leaf` calls `leaf_hash_to_index.insert(hash_B, leaf_A_index)`. Because `hash_B` already maps to `leaf_B_index`, `HashMap::insert` silently overwrites it. No error is returned. [6](#0-5) 

**Post-condition:**
- Blob: two live leaves both carrying `hash_B` (leaf_A and leaf_B).
- `leaf_hash_to_index`: one entry `{hash_B → leaf_A_index}` — leaf_B's mapping is gone.
- `key_to_index`: two entries `{key_A → leaf_A_index, key_B → leaf_B_index}` — structurally intact but hash cache is wrong.

### Impact Explanation

**`get_node_by_hash(hash_B)` returns wrong data.** It reads `leaf_hash_to_index` to get the index, then reads the blob at that index. After the corruption it returns `(key_A, new_val)` instead of `(key_B, val_B)`. [7](#0-6) 

**Merkle root is corrupted.** Two leaves share `hash_B`; the internal-node hashes computed from them are wrong, so the root hash no longer reflects the true key-value state.

**`check_integrity()` detects the corruption** via the `leaf_count != leaf_hash_to_index_cache_length` check (2 leaves, 1 cache entry), but this check is not called automatically after `upsert` in production (`check_integrity_on_drop` is only `true` in `cfg!(test)`). [8](#0-7) [9](#0-8) 

**Proof of inclusion for `key_B` is broken.** `get_proof_of_inclusion` uses `key_to_index` (still correct for key_B) but the sibling hashes along the path are now wrong because the root lineage was dirtied using `hash_B` for leaf_A. [10](#0-9) 

### Likelihood Explanation

The `MerkleBlob` API is a library; the hash is a caller-supplied parameter independent of the key-value data. The Python binding exposes `upsert(key, value, new_hash: bytes32)` with no restriction on `new_hash`. [11](#0-10) 

In normal DataLayer usage the hash is derived from the data (making a collision computationally infeasible). However, any code path that passes a caller-controlled or externally-sourced hash to `upsert` — including direct use of the Python binding — is immediately exploitable without any cryptographic work. The inconsistency with `insert` (which does guard) makes this a latent correctness defect that becomes a security issue the moment the hash is not strictly derived from the data.

### Recommendation

Add the same guard that `insert` already has, immediately after the key-lookup succeeds in the update branch:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    // NEW: reject if new_hash is already owned by a different leaf
    if leaf.hash != *new_hash && self.block_status_cache.contains_leaf_hash(new_hash) {
        return Err(Error::HashAlreadyPresent());
    }

    self.block_status_cache.remove_leaf(&leaf)?;
    ...
}
```

The guard must be placed **before** `remove_leaf` so that the cache is still consistent when the check runs.

### Proof of Concept

```rust
use chia_datalayer::merkle::blob::{MerkleBlob, InsertLocation};
use chia_datalayer::{KeyId, ValueId, Hash};

let mut blob = MerkleBlob::new(vec![]).unwrap();

let hash_a = Hash::from([0xAAu8; 32]);
let hash_b = Hash::from([0xBBu8; 32]);

blob.insert(KeyId(1), ValueId(10), &hash_a, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(20), &hash_b, InsertLocation::Auto {}).unwrap();

// Upsert key_A with key_B's existing hash — no error returned
blob.upsert(KeyId(1), ValueId(99), &hash_b).unwrap();

// leaf_hash_to_index now has 1 entry for 2 leaves → integrity failure
assert!(blob.check_integrity().is_err());

// get_node_by_hash(hash_b) returns key_A's data, not key_B's
let (key, val) = blob.get_node_by_hash(hash_b).unwrap();
assert_eq!(key, KeyId(1));   // wrong: should be KeyId(2)
assert_eq!(val, ValueId(99)); // wrong: should be ValueId(20)
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L199-207)
```rust
    fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
        let Some(index) = self.key_to_index.remove(&node.key) else {
            return Err(Error::UnknownKey(node.key));
        };
        self.leaf_hash_to_index.remove(&node.hash);

        self.free_indexes.insert(index);

        Ok(())
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L326-329)
```rust
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L372-374)
```rust
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L792-810)
```rust
    pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
        let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
            self.insert(key, value, new_hash, InsertLocation::Auto {})?;
            return Ok(());
        };

        self.block_status_cache.remove_leaf(&leaf)?;
        leaf.hash.clone_from(new_hash);
        leaf.value = value;
        // OPT: maybe just edit in place?
        block.node = Node::Leaf(leaf);
        self.insert_entry_to_blob(leaf_index, &block)?;

        if let Some(parent) = block.node.parent().0 {
            self.mark_lineage_as_dirty(parent)?;
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L868-874)
```rust
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1026)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1435-1440)
```rust
    #[pyo3(name = "upsert")]
    pub fn py_upsert(&mut self, key: KeyId, value: ValueId, new_hash: Hash) -> PyResult<()> {
        self.upsert(key, value, &new_hash)?;

        Ok(())
    }
```
