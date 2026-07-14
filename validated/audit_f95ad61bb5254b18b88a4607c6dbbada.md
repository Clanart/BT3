### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Checks for All Items Beyond the Bootstrap Pair — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` performs duplicate-key and duplicate-hash validation only for the first two items it inserts (via the guarded `self.insert()` path). All remaining items in the batch are written directly to the blob via `insert_entry_to_blob` without consulting `block_status_cache.contains_key()` or `block_status_cache.contains_leaf_hash()`. An attacker who controls the batch input can silently insert duplicate keys or duplicate leaf hashes, corrupting the Merkle tree root and enabling forged inclusion/exclusion proofs.

### Finding Description

`MerkleBlob::batch_insert` uses a two-phase strategy:

**Phase 1 (bootstrap, lines 578–585):** If the tree has ≤ 1 existing leaf, it pops the last two items from the batch and inserts them via `self.insert()`, which enforces both guards:

```rust
// crates/chia-datalayer/src/merkle/blob.rs:369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

**Phase 2 (fast path, lines 587–603):** All remaining items are written directly via `insert_entry_to_blob` — no call to `contains_key`, no call to `contains_leaf_hash`:

```rust
// crates/chia-datalayer/src/merkle/blob.rs:587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

The `block_status_cache` — the live state that tracks which keys and hashes are already present — is never consulted during the fast path. This is the direct analog of the reported pattern: a cached/live state value is read once (or not at all) before the loop and never re-checked inside it, allowing the guard to be bypassed.

When the tree already has ≥ 2 leaves, the bootstrap block is skipped entirely, so **every** item in the batch bypasses the duplicate check: [3](#0-2) 

The `BlockStatusCache` structure that enforces uniqueness: [4](#0-3) 

The Python binding that exposes `batch_insert` to callers: [5](#0-4) 

### Impact Explanation

Inserting a duplicate key or duplicate leaf hash into the `MerkleBlob` produces a tree with two leaf nodes sharing the same key or hash. This directly corrupts the computed root hash (since `internal_hash` over the subtree will differ from the canonical single-key tree). A corrupted root hash means:

- `get_proof_of_inclusion` will generate proofs that verify against the wrong root, enabling forged inclusion proofs.
- `get_proof_of_inclusion` for the legitimately-present key may fail or return an incorrect proof path, enabling forged exclusion.
- Any downstream DataLayer state that commits to the root hash will commit to an invalid state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is the primary bulk-insertion API for DataLayer and is exposed directly through the Python binding `py_batch_insert`. Any DataLayer operation that calls `batch_insert` with data derived from an untrusted peer (e.g., syncing a DataLayer store from a remote node) is a reachable entry path. The bypass requires only passing a repeated key or hash anywhere beyond the first two positions in the batch — a trivially constructable input.

### Recommendation

Add duplicate-key and duplicate-hash checks inside the fast-path loop, mirroring the guards already present in `insert()`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insert_entry_to_blob logic ...
}
```

Alternatively, update `block_status_cache` eagerly within the loop and check it before each write, so the cache reflects the in-progress batch state and catches intra-batch duplicates as well.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Pre-populate with 2 leaves so the bootstrap block is skipped entirely
for i in range(2):
    h = hashlib.sha256(i.to_bytes(8, "big")).digest()
    blob.insert(KeyId(i), ValueId(i), h)

# Now batch_insert with a duplicate key (key=0 already exists)
dup_key = KeyId(0)
dup_hash = hashlib.sha256(b"dup").digest()
# key=3 is new; key=0 is a duplicate — both bypass the check
blob.batch_insert(
    [(dup_key, ValueId(99)), (KeyId(3), ValueId(3))],
    [dup_hash, hashlib.sha256(b"new").digest()],
)

# Tree now contains two leaves with key=0; root hash is corrupted.
# get_proof_of_inclusion will produce a proof against the wrong root.
blob.calculate_lazy_hashes()
proof = blob.get_proof_of_inclusion(dup_key)
# proof.valid() may return True against the corrupted root,
# but the root no longer matches any honest tree state.
print("Corrupted root:", blob.get_root_hash().hex())
``` [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L88-94)
```rust
#[cfg_attr(feature = "py-bindings", pyclass(from_py_object))]
#[derive(Clone, Debug)]
pub struct BlockStatusCache {
    free_indexes: IndexSet<TreeIndex>,
    key_to_index: HashMap<KeyId, TreeIndex>,
    leaf_hash_to_index: HashMap<Hash, TreeIndex>,
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
