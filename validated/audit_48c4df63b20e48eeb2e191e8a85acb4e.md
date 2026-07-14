### Title
Missing Duplicate-Key/Hash Validation in `MerkleBlob::batch_insert` Corrupts DataLayer Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When the tree already contains ≥ 2 leaves, every item in the batch bypasses these checks entirely. An untrusted caller can supply duplicate `KeyId` or `Hash` values, silently inserting phantom leaf nodes into the blob. The resulting Merkle root is computed over a structurally corrupt tree, enabling forged inclusion proofs and committed state corruption.

### Finding Description

`MerkleBlob::insert` (the single-item path) enforces two guards before writing anything:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` has a split code path. When `leaf_count <= 1`, it pops the last two items from the input vector and routes them through `insert` (with checks). All remaining items — and **all** items when `leaf_count > 1` — are written directly via `insert_entry_to_blob` with no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // no guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which silently overwrites the existing `key_to_index` and `leaf_hash_to_index` entries:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);        // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index); // silent overwrite
}
``` [3](#0-2) 

The old leaf node remains physically in the blob but is no longer reachable through the cache. The tree's internal-node hashes are computed over the full blob structure (including the orphaned leaf), so `calculate_lazy_hashes` produces a root hash that does not correspond to the cache's view of the key-value set.

The Python binding `py_batch_insert` is the direct public entry point: [4](#0-3) 

### Impact Explanation

After a `batch_insert` with a duplicate key:

1. **Corrupted root hash** — `calculate_lazy_hashes` hashes over a tree that contains a phantom leaf. The committed root no longer faithfully represents the key-value set.
2. **Forged inclusion proofs** — `get_proof_of_inclusion` resolves the key through the cache (which points to the *new* leaf), but the root was computed over a tree that also contains the *old* leaf. A verifier checking the proof against the committed root will accept it, but the proof does not reflect the true state.
3. **Exclusion proof bypass** — a key that was supposedly deleted (overwritten by the duplicate) still participates in the root hash, so exclusion proofs for that key are invalid.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

The Python binding `py_batch_insert` is a public API callable by any DataLayer store owner. No privileged role is required. The attacker only needs to include a repeated `KeyId` (or `Hash`) in the batch list. The condition `leaf_count > 1` is trivially satisfied for any non-trivial store.

### Recommendation

Add the same duplicate-key and duplicate-hash guards at the top of `batch_insert` (or inside the fast-path loop) that `insert` already enforces:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
+   // Validate all inputs before mutating state
+   for ((key, _), hash) in &keys_values_hashes {
+       if self.block_status_cache.contains_key(*key) {
+           return Err(Error::KeyAlreadyPresent());
+       }
+       if self.block_status_cache.contains_leaf_hash(hash) {
+           return Err(Error::HashAlreadyPresent());
+       }
+   }
    ...
```

Alternatively, route all items through the existing `insert` method, accepting the performance trade-off, or maintain a local `HashSet` of keys/hashes seen within the batch to catch intra-batch duplicates as well.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate so leaf_count > 1 → all batch items bypass checks
blob.insert(KeyId(10), ValueId(10), h(10))
blob.insert(KeyId(20), ValueId(20), h(20))

# batch_insert with a duplicate key (KeyId(10) already present)
blob.batch_insert(
    [(KeyId(10), ValueId(999)), (KeyId(30), ValueId(30))],
    [h(999), h(30)],
)

blob.calculate_lazy_hashes()

# The root hash is now computed over a tree with TWO leaves for KeyId(10).
# get_proof_of_inclusion returns a proof for the *new* leaf (ValueId=999),
# but the root was hashed over a tree that still contains the *old* leaf (ValueId=10).
proof = blob.get_proof_of_inclusion(KeyId(10))
assert proof.valid()   # passes — but root is corrupt
```

The `insert` single-item path correctly rejects the duplicate: [5](#0-4) 

while `batch_insert` writes the duplicate leaf unconditionally: [6](#0-5) 

causing `add_leaf` to silently overwrite the cache entry and leave a phantom node in the blob: [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1013-1027)
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
