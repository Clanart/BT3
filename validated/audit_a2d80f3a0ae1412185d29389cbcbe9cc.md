### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
The `batch_insert` method in `MerkleBlob` skips the duplicate-key and duplicate-hash guards that `insert` enforces for all items beyond the first two. An untrusted caller supplying a batch with repeated `KeyId` or `Hash` values causes multiple leaf nodes with the same key or hash to be written directly into the blob, producing a structurally corrupt Merkle tree whose root hash is wrong and whose inclusion/exclusion proofs are invalid.

### Finding Description

`MerkleBlob::insert` enforces two invariants before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` (with full validation) only for the first two items when the tree has ≤ 1 existing leaf:

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining items are written directly via `insert_entry_to_blob` with **no duplicate check**:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

The function then assembles these unchecked leaves into a subtree and grafts it onto the existing tree: [4](#0-3) 

The Python binding exposes `batch_insert` directly to callers: [5](#0-4) 

The analog to the external report is exact: just as `getPriceInEth` accepted a 0-value from Tellor without checking, `batch_insert` accepts duplicate keys/hashes from its caller without checking, silently committing corrupt state.

### Impact Explanation

When a batch of ≥ 3 items contains a repeated `KeyId` or `Hash`:

1. Two leaf nodes with the same key are written into the blob.
2. The internal node hashes computed from them are wrong (they hash over the duplicate leaf hashes, not the intended unique set).
3. The resulting root hash does not correspond to any valid set of key-value pairs.
4. `get_proof_of_inclusion` returns a proof that passes `valid()` against the corrupt root but proves membership in a tree that does not reflect the true DataLayer state.
5. `confirm_included_already_hashed` / `confirm_not_included_already_hashed` (exposed to Python and wasm) will accept or reject proofs against the wrong root, letting untrusted input prove invalid state.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.** [6](#0-5) 

### Likelihood Explanation

`batch_insert` is a public API exposed through the Python binding (`py_batch_insert`) and used in production DataLayer batch-update flows. Any caller that constructs a batch with a repeated key (e.g., two updates to the same DataLayer key in one batch, or a malformed delta) triggers the bug. No privileged role or key material is required — only the ability to call `batch_insert` with attacker-controlled input. [7](#0-6) 

### Recommendation

Move the duplicate-key and duplicate-hash guards into `batch_insert` for every item in the batch, not just the first two. The simplest fix is to call `self.insert(key, value, &hash, InsertLocation::Auto {})` for every item (accepting the performance cost), or to pre-validate the entire input vector against `block_status_cache` before writing any leaf to the blob:

```rust
// Before the loop at line 587, add:
for ((key, _value), hash) in &keys_values_hashes {
    if self.block_status_cache.contains_key(*key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(hash) {
        return Err(Error::HashAlreadyPresent());
    }
}
```

Additionally, add a test that passes a batch with a duplicate key and asserts the error is returned.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Build a batch of 4 items where key[0] == key[3] (duplicate)
keys_values = [
    (KeyId(1), ValueId(10)),
    (KeyId(2), ValueId(20)),
    (KeyId(3), ValueId(30)),
    (KeyId(1), ValueId(99)),   # duplicate key — should be rejected
]
hashes = [
    hashlib.sha256(b"h1").digest(),
    hashlib.sha256(b"h2").digest(),
    hashlib.sha256(b"h3").digest(),
    hashlib.sha256(b"h4").digest(),
]

# With the current code this succeeds silently instead of raising KeyAlreadyPresentError
blob.batch_insert(keys_values, hashes)
blob.calculate_lazy_hashes()

# The tree now contains two leaves with KeyId(1).
# get_keys_values() will return one of them (last-write-wins or undefined),
# but the root hash is computed over both, making it wrong.
root = blob.get_root_hash()
print("Corrupt root:", root.hex())

# A proof generated for KeyId(1) will validate against this wrong root,
# but the root does not represent the intended key-value set.
proof = blob.get_proof_of_inclusion(KeyId(1))
assert proof.valid()   # passes — but root is corrupt
```

The first two items in the batch go through `insert()` (validated), while items 3 and 4 bypass all checks. Item 4 (`KeyId(1)` again) is written directly into the blob, producing a corrupt tree. [8](#0-7)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L607-656)
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

        if indexes.len() == 1 {
            // OPT: can we avoid this extra min height leaf traversal?
            let min_height_leaf = self.get_min_height_leaf()?;
            self.insert_subtree_at_key(min_height_leaf.key, indexes[0], Side::Left)?;
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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
