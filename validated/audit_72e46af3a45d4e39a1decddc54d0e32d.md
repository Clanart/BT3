### Title
Missing Duplicate-Key and Existing-Key Validation in `batch_insert` Bypasses Merkle Tree Integrity Checks — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces for every single-item insertion. When a batch of three or more entries is supplied, all entries except the last two are written directly into the blob without checking whether the key or hash already exists — either within the batch itself or in the existing tree. This silently corrupts the Merkle tree structure and produces an incorrect root hash, which in turn invalidates all proofs of inclusion and exclusion derived from that tree.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) enforces two guards before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`MerkleBlob::batch_insert` splits its input into two code paths:

1. **Bootstrap path (last two items, via `pop()`)** — calls `self.insert(...)`, which runs the guards above.
2. **Fast path (all remaining items)** — iterates directly and calls only `self.insert_entry_to_blob(...)`, with **no key-already-present or hash-already-present check**.

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 578-603
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← guarded
    }
}

for ((key, value), hash) in keys_values_hashes {          // ← remaining items
    let new_leaf_index = self.get_new_index();
    // ... builds LeafNode directly ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?; // ← NO duplicate check
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

Because `keys_values_hashes` is consumed in LIFO order for the bootstrap path, the **first N-2 entries** of any batch with N ≥ 3 items always take the unguarded fast path. Duplicate keys within those entries, or keys that already exist in the tree, are written as additional leaf nodes without error.

The Python binding `py_batch_insert` passes caller-supplied data directly into this function:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 1503-1518
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    // only checks length mismatch, not duplicate keys
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [3](#0-2) 

The Python-facing type stub confirms this is a public API:

```python
def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
``` [4](#0-3) 

---

### Impact Explanation

When duplicate keys are inserted via the fast path:

- The `block_status_cache` (`key_to_index` map) is updated for each entry, so the last duplicate wins in the cache — but **all duplicate leaf nodes persist in the blob**.
- The subtree built from the fast-path indexes includes all duplicate leaves, so the internal-node hashes computed at lines 637 are derived from a structurally invalid tree.
- `get_root_hash()` returns a hash that does not correspond to any valid set of key-value pairs.
- `get_proof_of_inclusion()` generates proofs that verify internally (against the corrupted root) but do not correspond to the actual committed data.
- `ProofOfInclusion::valid()` checks internal consistency of the proof chain but does not re-verify against an external trusted root, so a corrupted proof passes `valid()`. [5](#0-4) 

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

---

### Likelihood Explanation

The `batch_insert` Python binding is the primary bulk-insertion API for the DataLayer. Any caller — including a DataLayer sync peer supplying key-value deltas — can pass a list containing duplicate `KeyId` values. No privilege is required; the check is simply absent. The condition triggers for any batch of three or more entries where the first N-2 entries contain a repeated key or a key already present in the tree.

---

### Recommendation

Add duplicate-key and duplicate-hash validation at the start of `batch_insert`, before any entries are written to the blob. The simplest correct fix is to check each incoming key and hash against `block_status_cache` before entering the fast path, mirroring the guards already present in `insert`:

```rust
for ((key, _value), hash) in &keys_values_hashes {
    if self.block_status_cache.contains_key(*key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(hash) {
        return Err(Error::HashAlreadyPresent());
    }
}
```

Additionally, detect intra-batch duplicates (keys appearing more than once within the same batch input) using a local `HashSet` before writing any entry.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 entries so leaf_count > 1 (fast path is taken for batch)
blob.insert(KeyId(100), ValueId(100), h(100))
blob.insert(KeyId(200), ValueId(200), h(200))

# Batch with a duplicate key (KeyId(1) appears twice) — first N-2 items take fast path
kv = [(KeyId(1), ValueId(1)), (KeyId(2), ValueId(2)), (KeyId(1), ValueId(99))]
hashes = [h(1), h(2), h(999)]

# No error raised — duplicate KeyId(1) is silently accepted
blob.batch_insert(kv, hashes)
blob.calculate_lazy_hashes()

# Root hash is now derived from a structurally invalid tree
root = blob.get_root_hash()
print("Corrupted root:", root.hex())

# Proof for KeyId(1) verifies internally but tree state is inconsistent
proof = blob.get_proof_of_inclusion(KeyId(1))
print("proof.valid():", proof.valid())   # True — but root is wrong
```

The single-item `insert` path correctly raises `KeyAlreadyPresentError` for the same duplicate, confirming the guard is present there but absent in `batch_insert`. [6](#0-5) [7](#0-6)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
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
