### Title
Missing Duplicate-Key and Duplicate-Hash Constraint in `MerkleBlob::batch_insert` Silently Corrupts DataLayer Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` omits the duplicate-key and duplicate-hash guards for the bulk of its input. When the tree already contains ≥ 2 leaves the guards are skipped for **every** item in the batch; when the tree has 0–1 leaves they are skipped for all but the last two items. A caller — including via the publicly exposed Python binding `py_batch_insert` — can silently insert a `KeyId` that already exists in the tree, producing two leaf nodes with the same key, computing an incorrect Merkle root over the corrupted structure, and invalidating all subsequent proofs of inclusion/exclusion.

---

### Finding Description

`MerkleBlob::insert` (the single-item path) correctly enforces both guards before writing:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-373
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` delegates to `insert` only for the last two items popped from the vector **when `leaf_count <= 1`**. All remaining items are written directly via `insert_entry_to_blob` with no guard at all:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 578-602
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else {
            return Ok(());
        };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← guard here
    }
}

for ((key, value), hash) in keys_values_hashes {          // ← NO guard here
    let new_leaf_index = self.get_new_index();
    let new_block = Block { … Node::Leaf(LeafNode { key, value, hash, … }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

When `leaf_count > 1` the `if` block is skipped entirely, so **every** item in the batch bypasses both checks. The Python binding `py_batch_insert` only validates that the two input slices have equal length; it adds no key-uniqueness check of its own:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 1503-1518
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    if keys_values.len() != hashes.len() {
        Err(Error::UnmatchedKeysAndValues(…))?;
    }
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

The public Python stub confirms the method is part of the exported API:

```python
# wheel/python/chia_rs/datalayer.pyi  line 331
def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```

---

### Impact Explanation

Inserting a duplicate `KeyId` produces two leaf nodes in the blob that share the same key. The `block_status_cache` maps each key to exactly one index, so one leaf becomes unreachable through the cache while still occupying a slot in the blob. The internal-node hashes built over the batch are computed from the raw leaf hashes of both leaves, so the resulting root hash diverges from what a correct tree would produce. Any subsequent `get_proof_of_inclusion` call returns a proof anchored to this corrupted root; `proof.valid()` will either return `false` or verify against a forged root, directly satisfying the "corrupts tree roots / lets untrusted input prove invalid state" criterion.

---

### Likelihood Explanation

`py_batch_insert` is a public method on the `MerkleBlob` class exported in the `chia_rs.datalayer` submodule. Any Python caller that holds a `MerkleBlob` instance — including DataLayer nodes processing peer-supplied key-value batches — can pass a list containing a repeated `KeyId`. No privilege beyond access to the Python API is required. The single existing test (`test_batch_insert`) only exercises unique keys, so the gap is untested.

---

### Recommendation

Add the same guards that `insert` uses at the start of the main insertion loop in `batch_insert`, or perform a pre-flight uniqueness check over the entire input vector before writing any entry:

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

Alternatively, collect all incoming keys into a temporary `HashSet` and return an error on the first collision before any blob writes occur.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(blob=bytearray())

# Populate tree with 3 leaves so leaf_count > 1 (bypasses the guarded path)
for i in range(3):
    h = hashlib.sha256(i.to_bytes(8, "big")).digest()
    blob.insert(KeyId(i), ValueId(i), h)

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with KeyId(0) which already exists — no error is raised
dup_hash = hashlib.sha256(b"attacker").digest()
blob.batch_insert([(KeyId(0), ValueId(99))], [dup_hash])

blob.calculate_lazy_hashes()
root_after = blob.get_root_hash()

assert root_before != root_after   # root silently changed
blob.check_integrity()             # expected to raise IntegrityError
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
