### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Merkle Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards for all but the last two entries in the input batch. An unprivileged caller supplying a batch with repeated `KeyId` or `Hash` values (beyond the first two processed) silently inserts multiple leaf nodes with the same identity into the blob, producing a structurally corrupt tree whose root hash is wrong and whose proofs of inclusion are unreliable.

### Finding Description

`MerkleBlob::insert` enforces two invariants before writing a leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` (with those guards) only for the **last two** items it pops from the vector:

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining entries (the first `N-2` items in the original vector) are written directly via `insert_entry_to_blob`, which performs **no** duplicate check:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which silently overwrites the cache entry for a repeated key or hash:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index); // silent overwrite
}
``` [4](#0-3) 

The result is that the raw blob contains two (or more) leaf nodes sharing the same `KeyId` or `Hash`, while the cache only tracks the last one. Both duplicate leaves are wired into the internal-node tree built in the second phase of `batch_insert`, so the computed root hash covers the duplicate data.

The Python binding `py_batch_insert` exposes this path directly to any caller:

```rust
// lines 1503-1519
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

### Impact Explanation

A corrupted root hash means every downstream consumer that verifies DataLayer state against that root (e.g., via `get_proof_of_inclusion`) operates on a false commitment. Concretely:

- A proof generated for a duplicated key points to whichever leaf the cache last recorded; the other leaf is also present in the tree but unreachable through the cache, so its data is silently hidden.
- Because the root hash is wrong, a verifier comparing the on-chain root to an off-chain proof will either accept a proof for data that does not match the canonical state, or reject a valid proof — both constitute forged inclusion/exclusion.
- `check_integrity` will eventually detect the inconsistency (`IntegrityKeyToIndexCacheLength` or `IntegrityLeafHashToIndexCacheLength`), but only if explicitly called after the fact; the corruption is committed silently. [6](#0-5) 

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

### Likelihood Explanation

The Python binding `py_batch_insert` is a public API. Any caller — including a DataLayer client submitting a crafted batch — can supply a list containing a repeated `KeyId` or `Hash` in the first `N-2` positions. No privilege is required. The bug is triggered whenever `batch_insert` receives ≥ 3 entries and at least two of the first `N-2` share a key or hash (or collide with an already-present leaf).

### Recommendation

Add the same duplicate guards at the top of the bulk loop in `batch_insert` that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insert_entry_to_blob call
}
```

Alternatively, pre-deduplicate the input vector and return an error if duplicates are found, mirroring the contract of `insert`.

### Proof of Concept

```rust
use chia_datalayer::{Hash, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Build a batch of 4 entries where the first two share the same KeyId.
// batch_insert pops from the END, so items [0] and [1] bypass insert().
let dup_key = KeyId(42);
let batch = vec![
    ((dup_key,  ValueId(1)), Hash(Bytes32::new([0xAA; 32]))),
    ((dup_key,  ValueId(2)), Hash(Bytes32::new([0xBB; 32]))),
    ((KeyId(1), ValueId(3)), Hash(Bytes32::new([0xCC; 32]))),
    ((KeyId(2), ValueId(4)), Hash(Bytes32::new([0xDD; 32]))),
];

// Succeeds silently — no KeyAlreadyPresent error is raised.
blob.batch_insert(batch).unwrap();
blob.calculate_lazy_hashes().unwrap();

// check_integrity now fails: leaf_count (4) != key_to_index cache length (3).
assert!(blob.check_integrity().is_err());
``` [7](#0-6) [5](#0-4)

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
