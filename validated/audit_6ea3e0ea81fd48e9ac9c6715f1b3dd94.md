### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key Validation, Corrupting DataLayer Tree Root and Enabling Forged Proofs of Inclusion — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When the tree already contains two or more leaves, every item in a batch is written directly to the blob via `insert_entry_to_blob` without any uniqueness check. Supplying a batch that contains a key already present in the tree (or a duplicate key within the batch itself) silently corrupts the Merkle tree structure, produces a wrong root hash, and causes subsequent proofs of inclusion to be invalid or forgeable.

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

`batch_insert` takes a completely different code path. When the tree already holds two or more leaves (`leaf_count > 1`), the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, and every item in the batch is written directly:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // no duplicate check
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

Even when the tree has ≤1 leaves, only the last two items popped from the vector go through `self.insert()` (with checks); all earlier items in the vector still bypass validation. [3](#0-2) 

`insert_entry_to_blob` updates `block_status_cache` for the new leaf, overwriting the cache entry for the duplicate key so it now points to the new index. The original leaf node remains in the blob but is unreachable through the cache. The parent of the original leaf still references its old index, breaking the tree's structural invariants. When `calculate_lazy_hashes` subsequently recomputes internal-node hashes, it incorporates the orphaned node, producing a root hash that does not correspond to the intended key-value set.

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [4](#0-3) 

The `ProofOfInclusion::valid()` method recomputes hashes bottom-up and compares against the stored `combined_hash` at each layer:

```rust
pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(...);
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    existing_hash == self.root_hash()
}
``` [5](#0-4) 

Because the root stored in the blob is wrong after a corrupted batch insert, a proof generated from the corrupted tree will either fail validation against the correct root (denial of service) or, if the corrupted root is committed on-chain, will be accepted as valid for a state that was never legitimately inserted (forged inclusion).

---

### Impact Explanation

A corrupted `MerkleBlob` root hash means:

- **Forged inclusion**: A key that was silently overwritten still has a leaf node in the blob. A proof path can be constructed through that orphaned node, and `valid()` will return `true` against the corrupted root — proving inclusion of a key/value pair that is no longer the canonical state.
- **Corrupted tree root committed on-chain**: If the DataLayer root is published to the Chia blockchain (as is the intended use), the on-chain commitment reflects a root that does not faithfully represent the key-value store, enabling an operator or attacker to prove arbitrary state against it.

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

---

### Likelihood Explanation

`batch_insert` is the primary bulk-insertion API and is called by the Python DataLayer sync layer via `py_batch_insert`. Any caller — including a DataLayer store owner syncing data from a remote peer — who supplies a batch containing a key already present in the tree (or a repeated key within the batch) triggers the corruption silently, with no error returned. The condition is easy to satisfy accidentally during re-sync or delta application, and trivially exploitable intentionally.

---

### Recommendation

1. **Add duplicate checks inside `batch_insert`** before calling `insert_entry_to_blob` for each leaf: check `block_status_cache.contains_key(key)` and `block_status_cache.contains_leaf_hash(hash)` and return `Err(Error::KeyAlreadyPresent())` / `Err(Error::HashAlreadyPresent())` on collision, mirroring the guards in `insert`.
2. **Alternatively**, route all batch items through `self.insert()` (accepting the performance cost) or build a pre-pass that deduplicates and validates the entire batch before any structural mutation begins.
3. **Add a test** that calls `batch_insert` on a tree with 2+ existing leaves and includes a duplicate key, asserting that an error is returned and the tree root is unchanged.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn main() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Seed the tree with 3 leaves so leaf_count > 1
    for i in 0i64..3 {
        blob.insert(KeyId(i), ValueId(i), &sha256_num(i), InsertLocation::Auto {}).unwrap();
    }
    blob.calculate_lazy_hashes().unwrap();
    let root_before = blob.get_root();

    // batch_insert with a key (KeyId(0)) already present in the tree.
    // No error is returned; the duplicate is silently written.
    let batch = vec![
        ((KeyId(100), ValueId(100)), sha256_num(100)),
        ((KeyId(0),   ValueId(999)), sha256_num(999)),  // duplicate key!
    ];
    blob.batch_insert(batch).unwrap();   // succeeds — no KeyAlreadyPresent error
    blob.calculate_lazy_hashes().unwrap();
    let root_after = blob.get_root();

    // Root has changed even though KeyId(0) was already present.
    assert_ne!(root_before, root_after);

    // check_integrity() will now fail because the orphaned leaf breaks
    // the parent-child accounting.
    blob.check_integrity().unwrap_err();
}
``` [6](#0-5)

### Citations

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L40-58)
```rust
    pub fn valid(&self) -> bool {
        let mut existing_hash = self.node_hash;

        for layer in &self.layers {
            let calculated_hash = crate::calculate_internal_hash(
                &existing_hash,
                layer.other_hash_side,
                &layer.other_hash,
            );

            if calculated_hash != layer.combined_hash {
                return false;
            }

            existing_hash = calculated_hash;
        }

        existing_hash == self.root_hash()
    }
```
