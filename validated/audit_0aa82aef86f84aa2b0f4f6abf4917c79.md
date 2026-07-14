### Title
`batch_insert` Bypasses Duplicate-Key Guard, Corrupting DataLayer Merkle Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash checks that `MerkleBlob::insert` enforces for every item beyond the first two. An attacker who controls the key/value/hash tuples fed into a batch operation can silently insert duplicate leaf nodes, producing a structurally corrupt Merkle tree whose root hash is wrong and whose inclusion/exclusion proofs are unreliable.

### Finding Description

`MerkleBlob::insert` guards against re-insertion at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` (with its guards) only for the first two items when the tree has ≤ 1 existing leaf:

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining items are written directly to the blob via `insert_entry_to_blob` with **no duplicate check**:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

Two concrete scenarios trigger corruption:

1. **Duplicate within the batch itself** — two entries with the same `KeyId` or `Hash` in the input `Vec` beyond position 2 are both written as separate leaf nodes.
2. **Key already present in the tree** — when `leaf_count > 1`, every item in the batch bypasses the guard, so a key that already exists in the tree is inserted a second time.

In both cases the `block_status_cache` (`key_to_index` / `leaf_hash_to_index`) ends up inconsistent with the actual blob bytes: the cache records only one index per key (the last write wins), while the blob contains two leaf nodes for the same key. The internal-node hashes built over the corrupted subtree propagate the error all the way to the root.

This function is directly exposed to Python callers via `py_batch_insert`:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [4](#0-3) 

### Impact Explanation

The DataLayer Merkle tree is the authoritative data structure for proving inclusion and exclusion of key/value pairs in a DataLayer store. A corrupted root hash means:

- `get_proof_of_inclusion` returns proofs that pass `valid()` for a key at the wrong position, or fails for a key that genuinely exists.
- `get_proof_of_exclusion` (if used) can be made to accept or reject incorrectly.
- Any downstream consumer that trusts the root hash (e.g., on-chain coin puzzles that commit to a DataLayer root) will accept a forged state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is the primary bulk-load path used in production DataLayer sync (called from Python via `py_batch_insert`). Any DataLayer peer that can supply key/value/hash tuples to a syncing node — including a malicious DataLayer publisher — can craft a batch containing a duplicate key. The tree has ≤ 1 leaf only at initialization; once two entries exist, **every subsequent batch item** bypasses the guard, making the window wide open for any non-trivial store.

### Recommendation

Add the same duplicate checks at the top of the bulk loop in `batch_insert` that `insert` already performs:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insert_entry_to_blob logic ...
    self.block_status_cache.add_leaf(new_leaf_index, LeafNode { key, value, hash, ... });
}
```

Alternatively, refactor `batch_insert` to call the validated `insert` path for every item (accepting the small performance cost), or extract the guard logic into a shared helper called by both paths.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def h(n):
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(bytearray())

# Seed the tree with 3 entries so leaf_count > 1 and all batch items bypass the guard
blob.insert(KeyId(1), ValueId(1), h(1))
blob.insert(KeyId(2), ValueId(2), h(2))
blob.insert(KeyId(3), ValueId(3), h(3))

# Now batch_insert a duplicate of key=1 — no error is raised
blob.batch_insert(
    [(KeyId(4), ValueId(4)), (KeyId(1), ValueId(99))],  # KeyId(1) already present
    [h(4), h(99)],
)

# The blob now contains two leaf nodes for KeyId(1).
# The root hash is wrong; proof-of-inclusion for KeyId(1) is unreliable.
blob.calculate_lazy_hashes()
p = blob.get_proof_of_inclusion(KeyId(1))
# p.valid() may return True but points to the wrong (duplicate) leaf,
# while the original leaf is orphaned and the root is corrupted.
print("corrupt tree accepted duplicate key silently")
``` [5](#0-4)

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
