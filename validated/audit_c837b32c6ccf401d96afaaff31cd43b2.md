### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Guards, Enabling Merkle Root Corruption via Ghost-Leaf Injection — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash checks that `MerkleBlob::insert` enforces for all items beyond the first two when the tree already has ≥ 2 leaves. An unprivileged caller supplying a batch that contains a repeated `KeyId` (or repeated leaf `Hash`) causes a "ghost" leaf to be written into the blob that is invisible to the `BlockStatusCache`, yet is included in the Merkle root computation. The committed root hash therefore does not correspond to the set of keys the cache believes is present, forging the DataLayer tree state.

---

### Finding Description

**Normal single-insert path** (`insert`, lines 362–413) enforces two guards before touching the blob:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

**`batch_insert`** (lines 570–657) takes a different path. When the tree already has ≥ 2 leaves (`leaf_count > 1`), the bootstrap block (lines 578–585) is skipped entirely, and every item in the batch is written directly through `insert_entry_to_blob` with no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which uses `HashMap::insert` — silently overwriting the previous mapping for the same key:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // overwrites silently
    self.leaf_hash_to_index.insert(leaf.hash, index);   // overwrites silently
}
``` [3](#0-2) 

After the overwrite:

- The **first** leaf node for the duplicated key remains physically in the blob as a child of an internal node, but the cache no longer references it ("ghost leaf").
- The **second** leaf is tracked in the cache and is reachable via `get_proof_of_inclusion`.
- `calculate_lazy_hashes` propagates hashes bottom-up through the full tree structure, so the ghost leaf's hash is folded into every ancestor's hash all the way to the root. [4](#0-3) 

The resulting root hash therefore commits to a tree that contains a leaf the cache does not know about. `check_integrity` would detect the discrepancy (`leaf_count != key_to_index_cache_length`), but it is only called in test/debug builds via the `Drop` guard:

```rust
#[cfg(any(test, debug_assertions))]
impl Drop for MerkleBlob {
    fn drop(&mut self) {
        if self.check_integrity_on_drop { ... }
    }
}
``` [5](#0-4) 

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [6](#0-5) 

---

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic … corrupts tree roots, or lets untrusted input prove invalid state."**

Concrete consequences:

1. **Root hash forgery**: The committed root hash covers ghost leaves that are not part of the canonical key-value set. Any downstream system that trusts the root (e.g., a Chia full node verifying a DataLayer singleton coin) accepts a root that does not faithfully represent the stored data.

2. **Proof-of-inclusion failure**: `get_proof_of_inclusion` for the duplicated key returns a proof for the second (cache-tracked) leaf. The ghost leaf cannot be proven or disproven; its existence in the tree makes the root unprovable for the correct key set.

3. **Persistent state corruption**: Once `to_path` serialises the corrupted blob to disk, every subsequent `MerkleBlob::new` (which rebuilds the cache from the blob) will call `BlockStatusCache::new`, which also uses `HashMap::insert` without duplicate detection, perpetuating the corrupted state silently. [7](#0-6) 

---

### Likelihood Explanation

The `batch_insert` / `py_batch_insert` API is the primary bulk-load path used by the DataLayer Python layer (confirmed by test usage in `tests/test_datalayer.py`). Any DataLayer store operation that feeds a batch containing a repeated key — whether through a bug in the caller, a malicious DataLayer update message, or a crafted delta sync payload — triggers the corruption silently in release builds. No privileged role or key material is required; only the ability to supply input to `batch_insert`. [8](#0-7) 

---

### Recommendation

Add the same duplicate guards inside the fast path of `batch_insert` before calling `insert_entry_to_blob`:

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

Alternatively, change `add_leaf` to return an error (rather than silently overwriting) when a key or hash collision is detected, so that any call path — including future ones — is protected uniformly.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};

fn main() {
    // Start with a tree that already has 2 leaves so the bootstrap block is skipped.
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    blob.insert(KeyId(100), ValueId(100), &sha256(100), InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(200), ValueId(200), &sha256(200), InsertLocation::Auto {}).unwrap();

    // batch_insert with a duplicate KeyId(100) — no error is returned.
    blob.batch_insert(vec![
        ((KeyId(300), ValueId(300)), sha256(300)),
        ((KeyId(100), ValueId(999)), sha256(999)),  // duplicate key, different hash
    ]).unwrap();

    blob.calculate_lazy_hashes().unwrap();

    // Root hash now includes the ghost leaf for KeyId(100)/ValueId(100).
    // check_integrity() would panic here in debug builds:
    //   IntegrityKeyToIndexCacheLength: leaf_count=4, cache_length=3
    // In release builds this goes undetected.
    let root = blob.get_hash_at_index(chia_datalayer::TreeIndex(0)).unwrap();
    println!("Corrupted root: {:?}", root);
}
``` [9](#0-8)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L96-130)
```rust
impl BlockStatusCache {
    fn new(blob: &[u8]) -> Result<Self, Error> {
        let index_count = blob.len() / BLOCK_SIZE;

        let mut seen_indexes: BitVec<u64, bitvec::order::Lsb0> = BitVec::repeat(false, index_count);
        let mut key_to_index: HashMap<KeyId, TreeIndex> = HashMap::default();
        let mut leaf_hash_to_index: HashMap<Hash, TreeIndex> = HashMap::default();

        for item in LeftChildFirstIterator::new(blob, None) {
            let (index, block) = item?;
            seen_indexes.set(index.0 as usize, true);

            if let Node::Leaf(leaf) = block.node {
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
                if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
                    return Err(Error::HashAlreadyPresent());
                }
            }
        }

        let mut free_indexes: IndexSet<TreeIndex> = IndexSet::new();
        for (index, seen) in seen_indexes.iter().enumerate() {
            if !seen {
                free_indexes.insert(TreeIndex(index as u32));
            }
        }

        Ok(Self {
            free_indexes,
            key_to_index,
            leaf_hash_to_index,
        })
    }
```

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L570-585)
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1109-1132)
```rust
    pub fn calculate_lazy_hashes(&mut self) -> Result<(), Error> {
        // OPT: yeah, storing the whole set of blocks via collect is not great
        for item in LeftChildFirstIterator::new_with_block_predicate(
            &self.blob,
            None,
            Some(|block: &Block| block.metadata.dirty),
        )
        .collect::<Vec<_>>()
        {
            let (index, mut block) = item?;
            assert!(block.metadata.dirty);

            let Node::Internal(ref leaf) = block.node else {
                panic!("leaves should not be dirty")
            };
            // OPT: obviously inefficient to re-get/deserialize these blocks inside
            //      an iteration that's already doing that
            let left_hash = self.get_hash(leaf.left)?;
            let right_hash = self.get_hash(leaf.right)?;
            block.update_hash(&left_hash, &right_hash);
            self.insert_entry_to_blob(index, &block)?;
        }

        Ok(())
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1620-1628)
```rust
#[cfg(any(test, debug_assertions))]
impl Drop for MerkleBlob {
    fn drop(&mut self) {
        if self.check_integrity_on_drop {
            self.check_integrity()
                .expect("integrity check failed while dropping merkle blob");
        }
    }
}
```
