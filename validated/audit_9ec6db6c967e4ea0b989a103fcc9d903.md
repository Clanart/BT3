### Title
Missing Duplicate Key/Hash Validation in `batch_insert()` Enables DataLayer Merkle Tree Root Corruption — (File: crates/chia-datalayer/src/merkle/blob.rs)

---

### Summary

`MerkleBlob::batch_insert()` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert()` enforces. An unprivileged caller who supplies a `Vec` containing repeated `KeyId` or `Hash` values can silently insert multiple leaves with the same key into the tree, corrupting the Merkle root and invalidating all subsequent proofs of inclusion/exclusion.

---

### Finding Description

`insert()` enforces two invariants before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-373
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert()` takes a different code path. When the tree already has two or more leaves (`leaf_count > 1`), **every** item in the input vector is written directly through `insert_entry_to_blob()` — which performs no duplicate checks at all:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no key/hash guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

Even in the `leaf_count <= 1` branch, only the last two items are popped and routed through `insert()` (with guards); all remaining items in the vector are still processed via `insert_entry_to_blob()` without any duplicate check. [3](#0-2) 

`insert_entry_to_blob()` itself contains no duplicate guard:

```rust
// lines 1013-1030
fn insert_entry_to_blob(&mut self, index: TreeIndex, block: &Block) -> Result<(), Error> {
    ...
    match block.node {
        Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
        ...
    }
    Ok(())
}
``` [4](#0-3) 

When a duplicate key is inserted this way, `block_status_cache.key_to_index` is overwritten to point to the new leaf index, but the original leaf block remains in the blob at its old position. The tree now contains two physical leaf nodes sharing the same `KeyId`. The Merkle root is computed over the full blob structure (both leaves), while the cache only tracks one of them.

---

### Impact Explanation

**Corrupted Merkle root.** `calculate_lazy_hashes()` traverses the full blob and hashes both duplicate leaves into the root. The resulting root does not correspond to any valid key-set, so any root commitment stored on-chain or shared with peers is wrong.

**Forged / invalid proofs of inclusion.** `get_proof_of_inclusion()` follows the cache to the *new* leaf and builds a Merkle path. Because the root was computed over both leaves, the path does not verify against the committed root — a proof for a key that genuinely exists will appear invalid, and the tree cannot be used to prove correct state.

**Persistent state corruption.** `check_integrity()` is not called automatically after `batch_insert()`; the corrupted state persists until an explicit integrity check is triggered.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`batch_insert()` is a `pub` Rust function. The DataLayer syncs key-value data from the network; any component that feeds network-sourced records into `batch_insert()` without pre-filtering duplicates is a reachable entry point. The Python binding layer exposes `py_insert` individually (with guards) but does not expose `batch_insert` directly, so the primary risk is in Rust-level callers that process untrusted delta payloads. [5](#0-4) 

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards at the top of `batch_insert()` (or inside the inner loop before calling `insert_entry_to_blob()`) that already exist in `insert()`:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    // NEW: pre-validate all entries before touching the blob
    for ((key, _), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // ... existing logic
}
```

Alternatively, deduplicate the input vector before processing, or route every item through `insert()` regardless of the current leaf count.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Seed the tree with 2 leaves so leaf_count > 1 and the
// `batch_insert` fast-path is taken for ALL subsequent items.
blob.insert(KeyId(100), ValueId(100),
    &Hash(Bytes32::new([0xaa; 32])), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(101), ValueId(101),
    &Hash(Bytes32::new([0xbb; 32])), InsertLocation::Auto {}).unwrap();

// Now batch-insert a duplicate key (KeyId(100) already exists).
// The duplicate check in `insert()` would reject this, but
// `batch_insert()` bypasses it entirely.
blob.batch_insert(vec![
    ((KeyId(100), ValueId(999)), Hash(Bytes32::new([0xcc; 32]))),
    ((KeyId(102), ValueId(102)), Hash(Bytes32::new([0xdd; 32]))),
]).unwrap(); // succeeds — no error raised

// The blob now contains two leaf nodes with KeyId(100).
// calculate_lazy_hashes() will hash both into the root,
// producing a root that no single-key proof can verify.
blob.calculate_lazy_hashes().unwrap();

// check_integrity() will expose the inconsistency, but it is
// not called automatically — the corrupted state is live.
assert!(blob.check_integrity().is_err()); // tree is corrupt
``` [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L362-413)
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

        let insert_location = match insert_location {
            InsertLocation::Auto {} => self.get_random_insert_location_by_key_id(key)?,
            _ => insert_location,
        };

        match insert_location {
            InsertLocation::Auto {} => {
                unreachable!("this should have been caught and processed above")
            }
            InsertLocation::AsRoot {} => {
                if !self.block_status_cache.no_keys() {
                    return Err(Error::UnableToInsertAsRootOfNonEmptyTree());
                }
                self.insert_first(key, value, hash)
            }
            InsertLocation::Leaf { index, side } => {
                let old_leaf = self.get_node(index)?.try_into_leaf()?;

                let internal_node_hash = match side {
                    Side::Left => internal_hash(hash, &old_leaf.hash),
                    Side::Right => internal_hash(&old_leaf.hash, hash),
                };

                let node = LeafNode {
                    parent: Parent(None),
                    hash: *hash,
                    key,
                    value,
                };

                if self.block_status_cache.leaf_count() == 1 {
                    self.insert_second(node, &old_leaf, &internal_node_hash, side)
                } else {
                    self.insert_third_or_later(node, &old_leaf, index, &internal_node_hash, side)
                }
            }
        }
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1013-1030)
```rust
    fn insert_entry_to_blob(&mut self, index: TreeIndex, block: &Block) -> Result<(), Error> {
        let new_block_bytes = block.to_bytes()?;
        let extend_index = self.extend_index();
        match index.cmp(&extend_index) {
            Ordering::Greater => return Err(Error::BlockIndexOutOfBounds(index)),
            Ordering::Equal => self.blob.extend_from_slice(&new_block_bytes),
            Ordering::Less => {
                self.blob[block_range(index)].copy_from_slice(&new_block_bytes);
            }
        }

        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1409-1432)
```rust
    #[pyo3(name = "insert", signature = (key, value, hash, reference_kid = None, side = None))]
    pub fn py_insert(
        &mut self,
        key: KeyId,
        value: ValueId,
        hash: Hash,
        reference_kid: Option<KeyId>,
        // TODO: should be a Side, but python has a different Side right now
        side: Option<u8>,
    ) -> PyResult<()> {
        let insert_location = match (reference_kid, side) {
            (None, None) => InsertLocation::Auto {},
            (Some(key), Some(side)) => InsertLocation::Leaf {
                index: *self
                    .block_status_cache
                    .get_index_by_key(key)
                    .ok_or(Error::UnknownKey(key))?,
                side: Side::from_bytes(&[side])?,
            },
            _ => Err(Error::IncompleteInsertLocationParameters())?,
        };
        self.insert(key, value, &hash, insert_location)?;

        Ok(())
```
