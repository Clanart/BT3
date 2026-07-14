### Title
`MerkleBlob::batch_insert` Skips Duplicate-Key Check for Items Beyond the Second, Corrupting the DataLayer Merkle Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` applies a duplicate-key guard only to the first two items it processes (via the checked `insert()` path). All remaining items in the batch are written directly to the blob via `insert_entry_to_blob` with no key-uniqueness check. Supplying a batch that contains a repeated `KeyId` at position ≥ 3 silently inserts two leaf nodes with the same key into the tree, corrupts the `BlockStatusCache`, and produces a wrong Merkle root — enabling forged inclusion/exclusion proofs against the committed state.

### Finding Description

`MerkleBlob::insert` enforces two guards before writing a leaf:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` (with those guards) only for the first two items when the tree has ≤ 1 existing leaf:

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

Every subsequent item is written directly through `insert_entry_to_blob`, bypassing both guards entirely:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { parent: Parent(None), hash, key, value }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf(index, leaf)`, which updates the `key_to_index: HashMap<KeyId, TreeIndex>` cache. Because `HashMap::insert` silently overwrites, the cache ends up pointing only to the last-inserted leaf for the duplicated key, while the blob contains **two** leaf nodes with the same `KeyId` at different tree indexes. [4](#0-3) 

The subtree assembly loop then links both leaves into the tree and computes internal hashes over both: [5](#0-4) 

The resulting root hash is computed over a tree that violates the unique-key invariant. `check_integrity` will detect the mismatch (`leaf_count != key_to_index_cache_length`), but the corruption has already been committed to the blob. [6](#0-5) 

### Impact Explanation

A corrupted Merkle root means:

- `get_proof_of_inclusion` for the duplicated key returns a proof anchored to the cache-tracked leaf, but the actual root hash was computed over a tree that also contains the orphaned duplicate leaf. The proof is structurally inconsistent with the committed root.
- Any peer that receives and verifies the root against an honest tree will disagree, causing DataLayer state divergence.
- An attacker who controls the batch input can craft a tree whose root commits to a state that cannot be faithfully proven or disproven, enabling forged inclusion/exclusion proofs against the committed state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is a public API exposed through Python bindings and is the primary bulk-insertion path for DataLayer updates. Any caller — including one processing a delta received from a network peer — that supplies a batch with a repeated `KeyId` at index ≥ 3 triggers the corruption. No privilege is required beyond the ability to call `batch_insert` with attacker-chosen data.

### Recommendation

Add a duplicate-key check inside the fast-path loop in `batch_insert`, mirroring the guards already present in `insert()`:

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

Alternatively, refactor `batch_insert` to call `insert()` for all items, or pre-validate the entire input batch for uniqueness before any blob mutation begins.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def h(n):
    return hashlib.sha256(n.to_bytes(8, 'big')).digest()

blob = MerkleBlob(bytearray())

# Batch with 3 items: items 0 and 1 go through insert() (checked),
# item 2 (same KeyId as item 0) bypasses the check entirely.
batch_kv    = [(KeyId(10), ValueId(1)), (KeyId(20), ValueId(2)), (KeyId(10), ValueId(3))]
batch_hash  = [h(1), h(2), h(3)]

# This should raise KeyAlreadyPresent but does NOT — it silently corrupts the tree.
blob.batch_insert(batch_kv, batch_hash)
blob.calculate_lazy_hashes()

# The tree now contains two leaf nodes with KeyId(10).
# check_integrity() will report leaf_count != cache_length mismatch.
# Any proof-of-inclusion for KeyId(10) is anchored to a wrong root.
``` [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L607-648)
```rust
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
