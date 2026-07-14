### Title
`batch_insert` Silently Overwrites `key_to_index` / `leaf_hash_to_index` Cache Entries for Duplicate Keys, Corrupting the DataLayer Merkle Tree Root - (File: crates/chia-datalayer/src/merkle/blob.rs)

---

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a caller supplies duplicate `KeyId` or `Hash` values, `add_leaf` silently overwrites the `BlockStatusCache` mappings via `HashMap::insert`, leaving the blob with multiple leaf nodes sharing the same key while the cache tracks only the last one. The resulting Merkle root commits to data that cannot be proven, corrupting the DataLayer tree state.

---

### Finding Description

`MerkleBlob::insert` guards against duplicates before writing anything to the blob:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` calls `insert` for at most the last two items (only when `leaf_count <= 1`), then writes every remaining item directly via `insert_entry_to_blob` with **no duplicate check**:

```rust
// lines 587-602
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which uses plain `HashMap::insert` — silently overwriting any existing entry:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);        // overwrites silently
    self.leaf_hash_to_index.insert(leaf.hash, index); // overwrites silently
}
```

When `batch_insert` receives duplicate keys, two leaf nodes with the same `KeyId` are written to the blob, but `key_to_index` ends up pointing only to the last one. The blob and the cache are now inconsistent.

---

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Concrete consequences:

1. **Root hash corruption.** The root hash is computed over all leaves in the blob, including both duplicate leaves. The committed root therefore encodes data that the cache cannot serve — the "orphaned" duplicate leaf contributes to the root but is unreachable through any lookup.

2. **Proof-of-inclusion inconsistency.** `get_proof_of_inclusion` resolves the key through `key_to_index`, which points to only one of the two leaves. The other leaf is committed in the root but can never be proven, and no exclusion proof can be generated for it either.

3. **`check_integrity` failure.** The blob's leaf count (two nodes with the same key) diverges from `key_to_index.len()` (one entry), triggering `IntegrityKeyToIndexCacheLength` — but only if integrity is explicitly checked after the fact. The corruption is silently accepted at insertion time.

---

### Likelihood Explanation

`batch_insert` is a public API exposed directly to Python callers via `py_batch_insert` in the `#[pymethods]` block. Any Python caller that passes a list containing a repeated `KeyId` (or repeated `Hash`) triggers the bug. No privilege is required; the input is fully attacker-controlled. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Recommendation

Add duplicate-key and duplicate-hash checks inside `batch_insert` before calling `insert_entry_to_blob` for each item, mirroring the guards in `insert`:

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

Alternatively, make `add_leaf` return an error (instead of silently overwriting) when a key or hash already exists in the cache, so that all call sites are protected uniformly.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Pre-populate so leaf_count > 1 (bypasses the insert() path entirely)
for i in range(3):
    h = hashlib.sha256(i.to_bytes(8, 'big')).digest()
    blob.insert(KeyId(i), ValueId(i), h)

# batch_insert with a duplicate KeyId(0) — no error raised
dup_key = KeyId(0)
h_new = hashlib.sha256(b"new").digest()
blob.batch_insert([(dup_key, ValueId(99))], [h_new])

# The blob now has two leaf nodes for KeyId(0), but the cache
# only tracks one. The root hash is computed over both.
# check_integrity() will raise IntegrityKeyToIndexCacheLength.
blob.check_integrity()  # raises — tree is corrupted
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
