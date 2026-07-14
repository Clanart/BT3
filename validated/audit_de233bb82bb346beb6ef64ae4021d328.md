### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Uniqueness Checks, Enabling Merkle Tree Root Corruption - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
The `batch_insert` method in `MerkleBlob` omits the duplicate key and hash uniqueness checks that `insert` enforces. When the tree already has more than one leaf, every item in the batch bypasses these guards entirely and is written directly into the blob. Supplying duplicate keys or hashes — either within the batch itself or against keys already present in the tree — silently corrupts the Merkle tree structure and root hash, enabling forged or invalid DataLayer state proofs.

### Finding Description
`MerkleBlob::insert` enforces uniqueness before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` delegates to `insert` (and therefore inherits these checks) **only** for the first two items, and only when the tree currently holds ≤ 1 leaf:

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else {
            return Ok(());
        };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All remaining items — and **all** items when `leaf_count > 1` — are written directly via `insert_entry_to_blob` with no duplicate check whatsoever:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block {
        metadata: NodeMetadata { node_type: NodeType::Leaf, dirty: false },
        node: Node::Leaf(LeafNode { parent: Parent(None), hash, key, value }),
    };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

`insert_entry_to_blob` updates `BlockStatusCache` (via `add_leaf`), so inserting a duplicate key silently overwrites the cache's `key_to_index` and `leaf_hash_to_index` entries for that key. The original leaf node remains in the raw blob but is no longer reachable through the cache, producing a structurally inconsistent tree whose root hash is wrong and whose proofs are invalid.

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
// lines 1503-1518
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; Ok(()) }
``` [4](#0-3) 

No existing fuzz target exercises `batch_insert` with duplicate keys; all fuzz targets that test uniqueness use `insert` exclusively. [5](#0-4) 

### Impact Explanation
A `MerkleBlob` with duplicate keys produces an incorrect root hash. Any `ProofOfInclusion` generated from the corrupted tree will fail `valid()` for legitimately present keys, and the root hash committed on-chain will not match the actual data set. This directly satisfies the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.** [6](#0-5) 

### Likelihood Explanation
`batch_insert` / `py_batch_insert` is the primary bulk-load path used by the DataLayer node when syncing store updates from a DataLayer server. A malicious or compromised DataLayer server can include a key that already exists in the local store (or repeat a key within a single batch). Because the bulk path performs no check, the corruption occurs silently and `batch_insert` returns `Ok(())`. No special privilege is required; any peer that can supply DataLayer delta data can trigger this path.

### Recommendation
Add the same uniqueness guards to the bulk path of `batch_insert` that `insert` already enforces:

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

Alternatively, pre-validate the entire batch for internal duplicates and against the existing cache before writing any node, to preserve the atomicity guarantee of the bulk insert.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(blob=bytearray())

# Populate tree with 3 leaves so leaf_count > 1 (bulk path is always taken)
for i in range(3):
    blob.insert(KeyId(i), ValueId(i), hashlib.sha256(i.to_bytes(8, "big")).digest())

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with a key that already exists (key=0)
# insert() would raise KeyAlreadyPresentError; batch_insert silently succeeds
blob.batch_insert(
    [(KeyId(0), ValueId(999))],
    [hashlib.sha256(b"attacker_hash").digest()],
)

blob.calculate_lazy_hashes()
root_after = blob.get_root_hash()

# root hash has changed even though no legitimate new key was added
assert root_before != root_after, "tree root silently corrupted"

# proof for key=0 is now broken: cache points to the new (duplicate) leaf,
# the original leaf is orphaned in the blob, and the root is wrong
proof = blob.get_proof_of_inclusion(KeyId(0))
assert not proof.valid(), "proof is now invalid due to corruption"
```

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-585)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1503-1518)
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
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_insert.rs (L10-18)
```rust
    for (key, value, hash) in &args {
        match blob.insert(*key, *value, hash, InsertLocation::Auto {}) {
            Ok(_) => {}
            // should remain valid through these errors
            Err(Error::KeyAlreadyPresent()) => continue,
            Err(Error::HashAlreadyPresent()) => continue,
            // other errors should not be occurring
            Err(error) => panic!("unexpected error: {:?}", error),
        };
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
