### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting Tree Root and Enabling Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces when the tree already contains two or more leaves. An attacker who can supply crafted DataLayer delta input containing a key or hash already present in the tree causes the `block_status_cache` to silently overwrite its index entries, leaving a phantom duplicate leaf permanently embedded in the blob. The resulting tree has an incorrect root hash and produces unreliable proofs of inclusion, satisfying the "corrupts tree roots / lets untrusted input prove invalid state" impact criterion.

### Finding Description

**Root cause — missing guards in the fast path of `batch_insert`**

`MerkleBlob::insert` enforces two invariants before writing anything:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` uses `insert` only for the first two items when the tree has ≤1 existing leaf. For every subsequent item it calls `insert_entry_to_blob` directly, with no duplicate check at all:

```rust
// lines 587-602
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { … Node::Leaf(LeafNode { hash, key, value, … }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no guard
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` unconditionally calls `block_status_cache.add_leaf`, which does a plain `HashMap::insert` — silently overwriting any existing entry for the same key or hash:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silent overwrite
}
```

**Concrete corruption sequence**

Assume the tree already holds leaves `[A, B, C]` (leaf_count = 3 ≥ 2). Call `batch_insert([(A, new_val, new_hash), (D, val_D, hash_D)])`:

1. The fast path fires for both items.
2. A new leaf node for key `A` is written at `new_index_1`; `key_to_index[A]` is overwritten to `new_index_1`.
3. The original leaf for `A` (at `old_index_A`) is **still physically present** in the blob and still wired into the tree's parent/child pointer graph.
4. The batch subtree is attached to the existing tree via `insert_subtree_at_key`.
5. After `calculate_lazy_hashes`, the root hash is computed over **five** leaves (A-old, A-new, B, C, D), but `key_to_index` only tracks four.

Post-condition violations:
- `check_integrity` fails: `leaf_count (5) ≠ key_to_index.len() (4)`.
- `get_proof_of_inclusion(A)` returns a proof for `A-new`, but the root hash was computed including `A-old`; the proof is invalid against any externally stored root.
- A verifier holding the pre-corruption root can be shown a proof for `A-old` that is structurally valid against the corrupted tree, enabling forged state attestation.

**`upsert` has the same missing guard**

`upsert` removes the old leaf from the cache then calls `insert_entry_to_blob` with the new hash, again without checking `contains_leaf_hash(new_hash)`. If `new_hash` is already the hash of a different leaf, `add_leaf` silently overwrites that leaf's cache entry, making it unreachable by hash lookup while it remains in the blob.

```rust
// lines 792-809
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    …
    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    …
    self.insert_entry_to_blob(leaf_index, &block)?;   // ← no HashAlreadyPresent check
    …
}
```

### Impact Explanation

The DataLayer Merkle tree root is the authoritative commitment to a key-value store's state. Corrupting it means:

- **Forged inclusion proofs**: a proof for a stale or phantom leaf can be presented as valid against a root that was computed over duplicate leaves.
- **Exclusion proof bypass**: a key that should be absent (because it was "replaced") still has a live leaf in the blob, allowing a proof of its presence.
- **Cross-node state divergence**: nodes that accepted the corrupted blob will compute a different root than nodes that did not, causing permanent disagreement on DataLayer state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

The Python binding `py_batch_insert` is the primary entry point:

```rust
// lines 1503-1518
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    …
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

The only pre-call check is that `keys_values.len() == hashes.len()`; no deduplication is performed. Any DataLayer sync path that passes peer-supplied delta data directly to `batch_insert` (or `py_batch_insert`) without pre-filtering for duplicates is exploitable by a malicious peer. The DataLayer delta synchronization mechanism (`DeltaReader`, `collect_from_merkle_blobs`) is designed to accept data from remote nodes, making this a realistic external-attacker entry point.

### Recommendation

1. **`batch_insert`**: Add the same guards as `insert` at the top of the fast-path loop:
   ```rust
   if self.block_status_cache.contains_key(key) {
       return Err(Error::KeyAlreadyPresent());
   }
   if self.block_status_cache.contains_leaf_hash(&hash) {
       return Err(Error::HashAlreadyPresent());
   }
   ```
2. **`upsert`**: Before calling `insert_entry_to_blob`, check that `new_hash` is not already present for a *different* key:
   ```rust
   if self.block_status_cache.contains_leaf_hash(new_hash)
       && self.block_status_cache.get_index_by_leaf_hash(new_hash)
          != self.block_status_cache.get_index_by_key(key)
   {
       return Err(Error::HashAlreadyPresent());
   }
   ```
3. Add fuzz targets that exercise `batch_insert` with intentional duplicate keys/hashes against a pre-populated tree (the existing fuzz targets only use `insert`, which already rejects duplicates).

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn h(b: u8) -> Hash { Hash(Bytes32::new([b; 32])) }

let mut blob = MerkleBlob::new(vec![]).unwrap();
// Populate tree with 3 leaves so batch_insert takes the fast path
blob.insert(KeyId(1), ValueId(1), &h(1), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(2), &h(2), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(3), ValueId(3), &h(3), InsertLocation::Auto {}).unwrap();

// batch_insert with KeyId(1) already present — no error returned
blob.batch_insert(vec![
    ((KeyId(1), ValueId(99)), h(99)),  // duplicate key — should fail, doesn't
    ((KeyId(4), ValueId(4)),  h(4)),
]).unwrap();

blob.calculate_lazy_hashes().unwrap();

// check_integrity now fails: leaf_count in blob ≠ key_to_index cache length
blob.check_integrity().expect_err("tree is corrupted");
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L792-810)
```rust
    pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
        let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
            self.insert(key, value, new_hash, InsertLocation::Auto {})?;
            return Ok(());
        };

        self.block_status_cache.remove_leaf(&leaf)?;
        leaf.hash.clone_from(new_hash);
        leaf.value = value;
        // OPT: maybe just edit in place?
        block.node = Node::Leaf(leaf);
        self.insert_entry_to_blob(leaf_index, &block)?;

        if let Some(parent) = block.node.parent().0 {
            self.mark_lineage_as_dirty(parent)?;
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L860-874)
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
