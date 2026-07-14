### Title
Missing Duplicate-Key/Hash Validation in `batch_insert` Corrupts DataLayer Merkle Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a batch contains a key already present in the tree (or a duplicate within the batch itself), the function silently inserts a second leaf node for that key directly into the blob, corrupting the tree structure and producing an incorrect Merkle root. Proofs of inclusion generated after such a call are inconsistent with the actual tree root.

### Finding Description

`MerkleBlob::insert` enforces two guards before writing any data:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`MerkleBlob::batch_insert` calls `insert` (with those guards) only for the first one or two items when the tree has ≤ 1 existing leaves. All remaining items are written directly via `insert_entry_to_blob`, which performs no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {          // no guard here
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { parent: Parent(None), hash, key, value }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?; // writes unconditionally
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` calls `block_status_cache.add_leaf(index, leaf)`, which overwrites the cache entry for the key with the new index. The old leaf node remains physically present in the blob (still referenced by its parent internal node), but the cache no longer tracks it. The new leaf is then wired into the tree via `insert_subtree_at_key`. The result is a blob that contains two leaf nodes for the same key, while the cache only knows about one. The Merkle root is computed over the full blob (including the orphaned old leaf), so it diverges from what cache-based operations expect.

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

No test or fuzz target exercises `batch_insert` with duplicate keys; the existing `test_batch_insert` only uses sequential unique integers.

### Impact Explanation

After a `batch_insert` with a duplicate key:

1. The blob contains two leaf nodes for the same key; the internal-node parent of the old leaf still references it.
2. `block_status_cache.key_to_index` points only to the new leaf's index.
3. The Merkle root computed from the blob (via `calculate_lazy_hashes`) includes the orphaned old leaf, producing a root that does not match what `get_proof_of_inclusion` will reconstruct.
4. `check_integrity` detects the mismatch (`leaf_count != key_to_index_cache_length`) and fails, but only if explicitly called — it is not called automatically after `batch_insert`.
5. Any proof of inclusion generated for the affected key will carry a `root_hash()` that does not match the actual tree root, allowing an untrusted party to present a structurally valid but root-mismatched proof.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state**.

### Likelihood Explanation

The Python binding is the primary entry point for DataLayer operations. Any DataLayer update pipeline that calls `batch_insert` with a key that already exists in the store — whether due to a re-sync, a malicious peer supplying a crafted delta, or a programming error — will silently corrupt the tree. The absence of a fuzz target or test for this case means the gap has not been exercised.

### Recommendation

Add the same duplicate-key and duplicate-hash guards at the top of the `batch_insert` loop body (or delegate each item through `insert`). At minimum, check `block_status_cache.contains_key(key)` and `block_status_cache.contains_leaf_hash(&hash)` before calling `insert_entry_to_blob` for each item in the unchecked loop. A fuzz target analogous to `merkle_blob_insert.rs` should be added for `batch_insert` with potentially duplicate inputs.

### Proof of Concept

```rust
let mut blob = MerkleBlob::new(vec![]).unwrap();

// Pre-populate with 2 leaves so batch_insert skips the guarded path entirely
let k0 = KeyId(0); let k1 = KeyId(1);
blob.insert(k0, ValueId(0), &sha256_num(&0i64), InsertLocation::Auto{}).unwrap();
blob.insert(k1, ValueId(1), &sha256_num(&1i64), InsertLocation::Auto{}).unwrap();

// batch_insert with a key already present (k0) — no error returned
blob.batch_insert(vec![
    ((k0, ValueId(99)), sha256_num(&99i64)),   // duplicate key k0
    ((KeyId(2), ValueId(2)), sha256_num(&2i64)),
]).unwrap();  // succeeds silently

// Tree is now corrupted: two leaf nodes for k0 exist in the blob
blob.calculate_lazy_hashes().unwrap();
// check_integrity() will fail: leaf_count != key_to_index_cache_length
assert!(blob.check_integrity().is_err());

// Proof root_hash does not match actual tree root
let proof = blob.get_proof_of_inclusion(k0).unwrap();
// proof.valid() is true (internal consistency), but root_hash is wrong
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1013-1029)
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
