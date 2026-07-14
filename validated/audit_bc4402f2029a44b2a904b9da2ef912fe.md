### Title
Missing Duplicate-Key/Hash Guard in `MerkleBlob::batch_insert` Corrupts DataLayer Merkle Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash checks that `MerkleBlob::insert` enforces for all items beyond the first two. Supplying a batch that contains a repeated `KeyId` or `Hash` silently inserts both leaves into the blob, producing a structurally invalid Merkle tree whose root hash is wrong and whose proofs of inclusion are forged or unprovable.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) guards against duplicates at the very top of the function:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`MerkleBlob::batch_insert` calls `self.insert()` (with the guard) only for the first two items when the tree has ≤ 1 existing leaves:

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { return Ok(()); };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;   // ← guard active
    }
}
```

Every remaining item in the batch is written directly via `insert_entry_to_blob`, which performs **no duplicate check**:

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { … Node::Leaf(LeafNode { hash, key, value, … }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no guard
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` calls `block_status_cache.add_leaf(index, leaf)` for each new leaf. When a duplicate `KeyId` is inserted, `add_leaf` overwrites the cache entry for that key with the new index, while the original leaf node remains in the blob at its old index. The result is:

1. **Two leaf nodes with the same `KeyId`** exist in the blob; the cache only tracks the second.
2. The Merkle root is computed over both leaves, so it reflects a tree state that never legitimately existed.
3. `get_proof_of_inclusion` for the first (now cache-orphaned) leaf cannot be generated; for the second leaf it returns a proof against a corrupted root.
4. `check_integrity()` would detect the inconsistency (`leaf_count != key_to_index_cache_length`), but it is not called automatically inside `batch_insert`.

The Python binding `py_batch_insert` (lines 1503-1519) exposes this path directly to callers:

```rust
// lines 1503-1519
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    …
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

---

### Impact Explanation

A corrupted Merkle root means every subsequent proof of inclusion or exclusion produced by the tree is computed against a wrong root. A verifier that trusts the root (e.g., one that reads it from the DataLayer on-chain commitment) will either accept forged inclusion proofs or reject valid ones. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state**.

---

### Likelihood Explanation

`batch_insert` is the primary bulk-load path used in production (called from `py_batch_insert` and from DataLayer delta processing). Any caller that assembles a batch from on-chain DataLayer transaction data without pre-deduplicating keys — or any attacker who can craft a DataLayer transaction containing a repeated key — can trigger the corruption. The library itself provides no defence once the tree has ≥ 2 existing leaves.

---

### Recommendation

Add the same duplicate checks at the top of the fast path inside `batch_insert`, before writing any leaf to the blob:

```rust
for ((key, value), hash) in &keys_values_hashes {
    if self.block_status_cache.contains_key(*key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(hash) {
        return Err(Error::HashAlreadyPresent());
    }
}
```

Alternatively, update the cache speculatively during the loop and roll back on conflict, or deduplicate the input vector before processing. A regression test that calls `batch_insert` with a repeated `KeyId` and asserts `Err(Error::KeyAlreadyPresent())` should be added alongside the existing `test_double_insert_fails` test.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn main() {
    let mut blob = MerkleBlob::new(Vec::new()).unwrap();

    // Pre-populate with 2 leaves so the fast path is taken for subsequent inserts.
    for i in 0i64..2 {
        let h = Hash(Bytes32::new([i as u8; 32]));
        blob.insert(KeyId(i), ValueId(i), &h, InsertLocation::Auto {}).unwrap();
    }

    // Build a batch of 3 items where the first and third share the same KeyId.
    let dup_key = KeyId(99);
    let batch = vec![
        ((dup_key, ValueId(10)), Hash(Bytes32::new([10u8; 32]))),
        ((KeyId(100), ValueId(11)), Hash(Bytes32::new([11u8; 32]))),
        ((dup_key, ValueId(12)), Hash(Bytes32::new([12u8; 32]))),  // duplicate!
    ];

    // batch_insert succeeds silently — no error is returned.
    blob.batch_insert(batch).unwrap();

    // The tree is now corrupt: check_integrity fails.
    blob.check_integrity().unwrap_err();  // IntegrityKeyToIndexCacheLength or similar
}
```

The `batch_insert` call returns `Ok(())` despite the duplicate key, and the subsequent `check_integrity()` call exposes the corrupted state. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
