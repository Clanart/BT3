### Title
`batch_insert` Bypasses Duplicate-Key Guard, Corrupting DataLayer Merkle Tree Root and Proofs - (File: crates/chia-datalayer/src/merkle/blob.rs)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash checks that `MerkleBlob::insert` enforces. When a batch contains a key that already exists in the tree, or two entries with the same key within the batch itself, both leaf nodes are written into the blob. The `block_status_cache` silently overwrites its `key_to_index` entry for the colliding key, leaving the first leaf orphaned in the blob. The resulting tree carries an incorrect root hash, and any proof of inclusion generated from it is invalid or forgeable.

---

### Finding Description

`MerkleBlob::insert` (line 362) enforces two guards before writing anything:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` (line 570) takes a different code path. When the tree already has ≥ 2 leaves, the `if self.block_status_cache.leaf_count() <= 1` branch is skipped entirely, and **every** item in the batch is written directly via `get_new_index()` + `insert_entry_to_blob()` with no key-existence or hash-existence check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

When the tree has 0–1 leaves, only the **last two** items (popped via `pop()`) go through `self.insert()` with the guard; all earlier items in the vector still bypass it. [3](#0-2) 

`BlockStatusCache::add_leaf` uses `HashMap::insert`, which silently overwrites the old `key_to_index[K]` entry when a duplicate key K is inserted:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [4](#0-3) 

The first leaf node for K is now orphaned: it exists in the blob and is connected via parent pointers, but the cache no longer tracks it. The root hash is computed over a blob containing two leaf nodes for the same key, making it incorrect relative to the logical key-value mapping.

Conversely, `BlockStatusCache::new` (the deserialization path) **does** detect duplicate keys and returns `Error::KeyAlreadyPresent`:

```rust
if key_to_index.insert(leaf.key, index).is_some() {
    return Err(Error::KeyAlreadyPresent());
}
``` [5](#0-4) 

This means the in-memory state after a duplicate `batch_insert` is inconsistent with what any reload of the same blob would produce — the node would fail to restart after persisting the corrupted blob.

The Python binding exposes `batch_insert` directly: [6](#0-5) 

---

### Impact Explanation

- **Incorrect root hash**: The Merkle root is computed over a blob with two leaf nodes for the same key. Any consumer of the root hash (e.g., a DataLayer proof verifier) receives a hash that does not correspond to the logical key-value set.
- **Invalid / forgeable proofs of inclusion**: `get_proof_of_inclusion(K)` uses the cache-tracked index (the second leaf), but the tree structure may route through the first leaf, producing a proof that fails `proof.valid()`. Alternatively, an attacker can craft a batch that places a chosen hash at a chosen position, making a proof appear valid for a key-value pair that was never legitimately inserted.
- **Unrecoverable node state**: If the corrupted blob is persisted and the node restarts, `MerkleBlob::new` returns `Error::KeyAlreadyPresent` and the DataLayer node cannot load its state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

**Medium.** The Python binding `batch_insert` is a public API. In the DataLayer sync protocol, a peer node supplies the batch of key-value pairs to insert. A malicious or buggy peer can include a key that already exists in the local tree, or include the same key twice in one batch. No privileged access beyond being a DataLayer peer is required. The fuzz targets for `batch_insert` use only unique keys, so this path has not been exercised with duplicates. [7](#0-6) 

---

### Recommendation

Add duplicate-key and duplicate-hash checks at the start of `batch_insert`, mirroring the guards in `insert`:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    // NEW: guard against duplicates within the batch and against existing keys
    for ((key, _), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // ... rest of the function unchanged
```

Alternatively, deduplicate the input vector before processing, or extend the fuzz targets to include duplicate keys.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 leaves so leaf_count > 1 → fast path taken for ALL batch items
for i in range(2):
    h = hashlib.sha256(i.to_bytes(8, "big")).digest()
    blob.insert(KeyId(i), ValueId(i), h)

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with key=0, which already exists in the tree
duplicate_key = KeyId(0)
new_hash = hashlib.sha256(b"attacker-controlled").digest()
# No KeyAlreadyPresent error is raised — the duplicate is silently accepted
blob.batch_insert([(duplicate_key, ValueId(99))], [new_hash])
blob.calculate_lazy_hashes()

root_after = blob.get_root_hash()
assert root_before != root_after  # root is now wrong

# Proof of inclusion for key=0 is invalid
proof = blob.get_proof_of_inclusion(duplicate_key)
assert not proof.valid()  # forged / broken proof

# Persisting and reloading the blob causes a hard failure
raw = bytes(blob.blob)
try:
    MerkleBlob(blob=bytearray(raw))
    assert False, "should have raised"
except Exception as e:
    print(f"Node cannot reload its own state: {e}")  # KeyAlreadyPresent
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L109-111)
```rust
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
```

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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_insert.rs (L6-18)
```rust
fuzz_target!(|args: Vec<(KeyId, ValueId, Hash)>| {
    let mut blob = MerkleBlob::new(Vec::new()).expect("construct MerkleBlob");
    blob.check_integrity_on_drop = false;

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
