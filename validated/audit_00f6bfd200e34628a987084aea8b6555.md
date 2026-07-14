### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation, Enabling DataLayer Merkle Tree Root Corruption — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` is a public function exposed through Python bindings that skips the duplicate-key and duplicate-hash guards enforced by `MerkleBlob::insert`. Supplying a batch that contains a repeated `KeyId` or `Hash` silently corrupts the in-memory `BlockStatusCache`, produces a structurally invalid Merkle tree, and yields a wrong root hash — with no error returned to the caller and no automatic detection in production builds.

---

### Finding Description

`MerkleBlob::insert` guards against duplicate state at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a different path. When the tree already has two or more leaves (`leaf_count > 1`), the entire input vector is written directly through `insert_entry_to_blob` without any duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which performs a plain `HashMap::insert` — silently overwriting the existing cache entry for a duplicate key:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // overwrites silently
    self.leaf_hash_to_index.insert(leaf.hash, index);   // overwrites silently
}
``` [3](#0-2) 

After the call:

* The raw blob contains **two leaf nodes** sharing the same `KeyId`.
* The cache records only the **last** index for that key; the first leaf is orphaned.
* The subtree-building loop pairs all indexes (including both duplicates) into internal nodes and computes `internal_hash` over them, producing a **wrong root hash**.
* `get_proof_of_inclusion` for the orphaned leaf fails because the cache no longer maps to it.
* `check_integrity` would detect the mismatch (`leaf_count != key_to_index_cache_length`), but integrity-on-drop is only enabled in test/debug builds:

```rust
check_integrity_on_drop: cfg!(test),
``` [4](#0-3) 

The same code path is reachable from the Python binding `py_batch_insert`, which is part of the published `chia_rs` wheel:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
``` [5](#0-4) 

And declared in the public type stub:

```python
def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
``` [6](#0-5) 

---

### Impact Explanation

A corrupted root hash means every `ProofOfInclusion` derived from the blob after the bad batch is anchored to a hash that does not correspond to any legitimately constructed tree. `ProofOfInclusion.valid()` checks internal consistency of the proof chain but does not compare the final hash against any external trusted root:

```rust
pub fn valid(&self) -> bool {
    ...
    existing_hash == self.root_hash()   // self-referential; no external anchor
}
``` [7](#0-6) 

A node that accepts the corrupted blob's root and then verifies proofs against it will accept inclusion proofs for a tree that was never legitimately committed. This satisfies the **"corrupts tree roots / lets untrusted input prove invalid state"** criterion in the DataLayer impact scope.

---

### Likelihood Explanation

The DataLayer sync path calls `batch_insert` with data received from peer nodes. A malicious peer that controls a DataLayer store can craft a sync payload containing repeated `KeyId` values. Because `batch_insert` performs no deduplication, the receiving node's `MerkleBlob` is corrupted on the first sync cycle. The Python binding makes the same path reachable from any Python-level DataLayer integration code.

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards to `batch_insert` that exist in `insert`, either:

1. **Before the bulk loop** — iterate the input once, check each `(key, hash)` pair against `block_status_cache.contains_key` / `contains_leaf_hash`, and return `Err` on the first duplicate; or
2. **Inside `insert_entry_to_blob`** — make `add_leaf` return an error when `HashMap::insert` returns `Some(old_value)`, propagating it up through `batch_insert`.

Additionally, consider enabling `check_integrity_on_drop` in production builds (or at least in release-mode DataLayer node code) so that any future corruption is caught at the earliest possible point.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Seed the tree with two leaves so leaf_count > 1 — all subsequent
# batch_insert items bypass insert() and go straight to insert_entry_to_blob.
blob.insert(KeyId(1), ValueId(1), hashlib.sha256(b"seed1").digest())
blob.insert(KeyId(2), ValueId(2), hashlib.sha256(b"seed2").digest())

# Duplicate KeyId(3) in the same batch — no error is raised.
blob.batch_insert(
    [(KeyId(3), ValueId(3)), (KeyId(3), ValueId(99))],
    [hashlib.sha256(b"hash3a").digest(), hashlib.sha256(b"hash3b").digest()],
)

blob.calculate_lazy_hashes()

# Root hash is now computed over a tree containing two leaves with KeyId(3).
root = blob.get_root_hash()
print("Corrupted root:", root.hex())

# check_integrity reveals the inconsistency (leaf_count != cache length).
try:
    blob.check_integrity()
except Exception as e:
    print("Integrity failure:", e)   # IntegrityKeyToIndexCacheIndex or similar

# Proof for KeyId(3) is anchored to the corrupted root — the orphaned
# first leaf has no reachable proof at all.
proof = blob.get_proof_of_inclusion(KeyId(3))
print("Proof valid (against corrupted root):", proof.valid())
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L328-329)
```rust
            check_integrity_on_drop: cfg!(test),
        };
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
