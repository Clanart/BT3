### Title
`MerkleBlob::upsert` Skips Hash-Uniqueness Pre-Condition Check, Enabling Duplicate-Hash Leaf Insertion That Corrupts Tree Root and Forges Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::upsert` transitions an existing leaf to a new hash without first verifying that the new hash is not already present in the tree for a different key. The `insert` path enforces `HashAlreadyPresent` before writing, but the update branch of `upsert` removes the old leaf from the cache and then calls `insert_entry_to_blob` directly, bypassing the uniqueness guard entirely. Supplying a `new_hash` that already belongs to another leaf silently overwrites the `leaf_hash_to_index` cache entry, leaving two blob nodes with identical hashes. The resulting tree root is cryptographically invalid, and any proof of inclusion derived from it is forged.

---

### Finding Description

`insert` enforces two invariants before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`upsert`, when the key already exists, takes a completely different path that omits both guards:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 792-810
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    self.block_status_cache.remove_leaf(&leaf)?;   // removes OLD hash from cache
    leaf.hash.clone_from(new_hash);                // sets NEW hash — no uniqueness check
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?; // writes new hash into cache silently
    ...
}
``` [2](#0-1) 

`insert_entry_to_blob` dispatches to `block_status_cache.add_leaf`, which calls `HashMap::insert` — a silent overwrite:

```rust
// lines 1024-1026
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    ...
}
``` [3](#0-2) 

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index); // silently overwrites existing entry
}
``` [4](#0-3) 

**Step-by-step corruption when `upsert(key_A, value, hash_B)` is called and `hash_B` already belongs to `key_B`:**

1. `remove_leaf` removes `key_A`'s old hash from `leaf_hash_to_index`.
2. `add_leaf` inserts `hash_B → index_of_key_A`, overwriting the existing `hash_B → index_of_key_B` mapping.
3. The raw blob now contains **two leaf nodes with identical hashes** (`key_A` and `key_B` both carry `hash_B`).
4. `leaf_hash_to_index.len()` = N−1 while `key_to_index.len()` = N — cache is inconsistent.
5. All internal-node hashes on the path from `key_A`'s leaf to the root are recomputed from the duplicate hash, producing a **cryptographically invalid root**.
6. `get_proof_of_inclusion(key_B)` returns a proof that verifies against the wrong position.
7. `get_node_by_hash(hash_B)` returns `key_A`'s data instead of `key_B`'s data.

The `check_just_integrity` function would eventually detect the cache-length mismatch via `IntegrityLeafHashToIndexCacheLength`, but integrity is not checked automatically after every mutation — it is only called on explicit request or on drop when `check_integrity_on_drop` is set: [5](#0-4) 

The corrupted blob is persisted to disk and used for proofs before any integrity check fires.

---

### Impact Explanation

The corrupted tree root is committed to the DataLayer store. Any proof of inclusion derived from this root is forged — it proves membership of a key at a position that does not correspond to the correct hash. `get_node_by_hash` returns wrong key/value pairs. The `leaf_hash_to_index` cache diverges from the blob, so subsequent operations that rely on hash-based lookup (`get_proof_of_inclusion`, `get_node_by_hash`, delta synchronization) operate on stale or wrong data.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

---

### Likelihood Explanation

`upsert` is exposed directly via the Python wheel bindings with a fully user-controlled `new_hash` parameter:

```python
def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
``` [6](#0-5) 

All hashes already present in the tree are public (stored in the blob, readable via `get_hashes_indexes` or `get_proof_of_inclusion`). An attacker who controls a key in the DataLayer store can call `upsert` with any existing hash to trigger the corruption. No special privileges are required — only the ability to call `upsert` on a `MerkleBlob` instance, which is the normal DataLayer write path.

---

### Recommendation

Add a hash-uniqueness pre-condition check in `upsert` before removing the old leaf, mirroring the guard in `insert`. The check must be performed **before** `remove_leaf` to avoid a false negative (removing the old hash first would make `contains_leaf_hash` miss the collision):

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

+   // Reject if the new hash already belongs to a *different* key
+   if leaf.hash != *new_hash && self.block_status_cache.contains_leaf_hash(new_hash) {
+       return Err(Error::HashAlreadyPresent());
+   }

    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?;
    ...
}
```

The same missing guard exists in `batch_insert` (lines 587–603), which also calls `insert_entry_to_blob` directly for the bulk of the batch without checking for duplicate keys or hashes within the batch or against the existing tree state. [7](#0-6) 

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(vec![]).unwrap();

let hash_a = Hash(Bytes32::new([0xAA; 32]));
let hash_b = Hash(Bytes32::new([0xBB; 32]));

// Insert two leaves with distinct hashes
blob.insert(KeyId(1), ValueId(1), &hash_a, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(2), &hash_b, InsertLocation::Auto {}).unwrap();

// Upsert key 1 with key 2's existing hash — no error is returned
blob.upsert(KeyId(1), ValueId(99), &hash_b).unwrap();

// Two leaves now share hash_b; cache is inconsistent; root is invalid
// check_integrity() returns IntegrityLeafHashToIndexCacheLength(2, 1)
assert!(blob.check_integrity().is_err());

// Proof of inclusion for key 2 is now forged / invalid
let proof = blob.get_proof_of_inclusion(KeyId(2)).unwrap();
assert!(!proof.valid()); // proof verifies against wrong hash chain
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L868-874)
```rust
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }
```

**File:** wheel/python/chia_rs/datalayer.pyi (L323-323)
```text
    def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
```
