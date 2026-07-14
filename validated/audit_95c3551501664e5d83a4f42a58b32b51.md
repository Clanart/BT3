### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Tree Roots — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a batch containing a key or hash already present in the tree is processed through the bulk path, `insert_entry_to_blob` silently overwrites the `BlockStatusCache` entry, leaving a "ghost" leaf node in the flat blob that is structurally part of the tree (and therefore part of the root-hash computation) but is no longer reachable through the cache. The committed root hash therefore diverges from the key-value set the DataLayer believes it has committed, enabling inconsistent proofs of inclusion and exclusion.

### Finding Description

`MerkleBlob::insert` guards against duplicates before touching the blob:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a completely different code path for all items beyond the first two (or for all items when the tree already has ≥ 2 leaves). It calls `insert_entry_to_blob` directly, with **no** `contains_key` or `contains_leaf_hash` check:

```rust
// lines 587-602 — no duplicate guard
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which silently overwrites the existing `key_to_index` and `leaf_hash_to_index` entries via `HashMap::insert`:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index); // silent overwrite
}
``` [3](#0-2) 

The original leaf node at its old index is now a **ghost**: it remains in the flat blob and is wired into the tree's parent/child structure (so it contributes to every ancestor's hash), but the cache no longer tracks it. Consequently:

1. The root hash is computed over a tree that contains the ghost leaf.
2. `get_keys_values()` and `get_proof_of_inclusion()` operate only on cache-tracked leaves, so they see a different set.
3. `check_integrity` would detect the discrepancy (`leaf_count` from blob traversal > `key_to_index_cache_length`), but `check_integrity` is not called automatically on every mutation. [4](#0-3) 

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
// lines 1503-1518
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
``` [5](#0-4) 

### Impact Explanation

The DataLayer Merkle tree root is what gets committed on-chain via a singleton. A corrupted root (one that includes ghost leaves) means:

- The on-chain commitment does not correspond to the key-value set the store operator believes it committed.
- Proofs of inclusion for ghost-leaf keys can be generated and will validate against the corrupted root, even though those keys are not in the logical store.
- Proofs of exclusion for keys that are logically present but whose cache entry was overwritten may incorrectly succeed.

This satisfies the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots and lets untrusted input prove invalid state**.

### Likelihood Explanation

The `py_batch_insert` Python binding is a public API surface. Any caller that passes a list containing a duplicate `KeyId` (or a `Hash` already present in the tree) triggers the corruption silently. The `insert` API correctly rejects such inputs with `KeyAlreadyPresent` / `HashAlreadyPresent`, so callers who migrate from `insert` to `batch_insert` for performance may not anticipate the missing validation. The fuzz targets for `batch_insert` do not exercise duplicate-key inputs in the batch itself. [6](#0-5) 

### Recommendation

Add the same pre-insertion guards to the bulk path inside `batch_insert` that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing bulk insertion logic
}
```

Alternatively, validate the entire input batch for uniqueness before any mutation begins, so the tree is never left in a partially-corrupted state on error.

### Proof of Concept

```rust
// Tree already has 2 leaves (so leaf_count > 1, bypassing the insert() path)
let mut blob = MerkleBlob::new(vec![]).unwrap();
blob.insert(KeyId(0), ValueId(0), &hash_0, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(1), ValueId(1), &hash_1, InsertLocation::Auto {}).unwrap();

// batch_insert with a duplicate key — no error returned
blob.batch_insert(vec![
    ((KeyId(2), ValueId(2)), hash_2),
    ((KeyId(2), ValueId(99)), hash_3), // duplicate key, different hash
]).unwrap(); // succeeds silently

// Root hash now includes a ghost leaf for KeyId(2) at the first index.
// check_integrity reveals the corruption:
blob.check_integrity().unwrap_err(); // IntegrityKeyToIndexCacheLength
``` [7](#0-6)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-874)
```rust
        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_insert_and_delete.rs (L21-28)
```rust
            match blob.insert(key, value, &hash, InsertLocation::Auto {}) {
                Ok(_) => {}
                // should remain valid through these errors
                Err(Error::KeyAlreadyPresent()) => continue,
                Err(Error::HashAlreadyPresent()) => continue,
                // other errors should not be occurring
                Err(error) => panic!("unexpected error while inserting: {:?}", error),
            };
```
