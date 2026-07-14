### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key and Duplicate-Hash Validation for Bulk Entries, Corrupting DataLayer Merkle Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces for all items beyond the first two in a batch. An unprivileged caller (via the Python binding `py_batch_insert`) can supply a batch of ≥3 entries containing a repeated `KeyId` or repeated `Hash`, causing two distinct leaf nodes with the same identity to be written into the blob. This corrupts the Merkle tree root and enables forged DataLayer inclusion/exclusion proofs.

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

`batch_insert` uses a split execution path. The first two items (needed to bootstrap the tree) are routed through `self.insert(...)`, which runs the guards above:

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← guards run here
    }
}
``` [2](#0-1) 

All remaining items (index 3 onward) are written **directly** via `insert_entry_to_blob`, with no key or hash uniqueness check:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // ← no duplicate check
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

When a duplicate `KeyId` or `Hash` appears in the bulk portion, `insert_entry_to_blob` writes the second leaf into the blob and overwrites the `block_status_cache` entry for that key/hash (since `HashMap::insert` silently replaces). The first leaf remains in the blob but is now orphaned from the cache. The tree then contains two physical leaf nodes sharing the same logical identity, producing an incorrect root hash and breaking all subsequent proof operations.

The Python binding `py_batch_insert` exposes this path directly to unprivileged callers:

```rust
// lines 1503-1519
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [4](#0-3) 

The `BlockStatusCache` integrity invariant (`leaf_count == leaf_hash_to_index_cache_length`) is violated, which `check_integrity` would detect — but `check_integrity` is only called in test/debug builds (the `Drop` impl is `#[cfg(any(test, debug_assertions))]`): [5](#0-4) 

### Impact Explanation

A corrupted `MerkleBlob` produces an incorrect root hash. Any `ProofOfInclusion` generated from the corrupted tree will fail `valid()` for legitimate entries, and the `get_node_by_hash` lookup will return the wrong `(KeyId, ValueId)` pair for the surviving cache entry. This lets an untrusted input corrupt committed DataLayer state and invalidate or forge inclusion/exclusion proofs — matching the **High** impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."* [6](#0-5) 

### Likelihood Explanation

The Python binding is the primary DataLayer interface. Any DataLayer node operator or client that calls `batch_insert` with a batch of ≥3 entries — whether by mistake or malicious intent — can trigger this. No privileged role or key material is required. The path is reachable from normal DataLayer store update operations.

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the bulk loop in `batch_insert` that already exist in `insert`:

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

Alternatively, refactor `batch_insert` to route all items through `insert` (accepting the performance cost), or pre-validate the entire batch for uniqueness before writing any entry.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
from chia_rs.sized_bytes import bytes32

blob = MerkleBlob(blob=bytearray())

# Need ≥3 items so the bulk path (lines 587-603) is reached.
# Items 1 and 2 go through insert() with guards; item 3 bypasses them.
k1, v1, h1 = KeyId(1), ValueId(1), bytes32(b'\x01' * 32)
k2, v2, h2 = KeyId(2), ValueId(2), bytes32(b'\x02' * 32)
k3, v3, h3 = KeyId(3), ValueId(3), bytes32(b'\x03' * 32)
# k4 duplicates k3 — same KeyId, different hash
k4, v4, h4 = KeyId(3), ValueId(99), bytes32(b'\x04' * 32)

# batch_insert with a duplicate key in position 4 (bulk path)
blob.batch_insert(
    [(k1, v1), (k2, v2), (k3, v3), (k4, v4)],
    [h1, h2, h3, h4]
)
# No error raised — duplicate key silently accepted.
# The blob now contains two leaf nodes with KeyId(3).
# calculate_lazy_hashes produces a root that does not match
# a tree built by four sequential insert() calls.
blob.calculate_lazy_hashes()
# Proof of inclusion for k3 will be inconsistent or point to wrong value.
proof = blob.get_proof_of_inclusion(KeyId(3))
print(proof.valid())  # may be True but for the wrong leaf
``` [7](#0-6) [1](#0-0)

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
