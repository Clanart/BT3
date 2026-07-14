### Title
`MerkleBlob::upsert` Bypasses `HashAlreadyPresent` Guard Enforced by `insert`, Enabling Duplicate Leaf Hashes and Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::insert` enforces a `HashAlreadyPresent` uniqueness check before adding a new leaf. `MerkleBlob::upsert` — the update path for an existing key — removes the old hash from the cache and writes a new hash to the blob without performing the same uniqueness check. An attacker who can supply an arbitrary hash to `upsert` (e.g., via a malicious DataLayer delta) can produce two leaves with identical hashes, corrupting the Merkle tree root and enabling forged inclusion proofs.

---

### Finding Description

`MerkleBlob::insert` explicitly rejects any hash that is already tracked in the `block_status_cache`:

```rust
// blob.rs lines 362–374
pub fn insert(...) -> Result<TreeIndex, Error> {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(hash) {   // ← guard
        return Err(Error::HashAlreadyPresent());
    }
    ...
}
``` [1](#0-0) 

`MerkleBlob::upsert` takes a different path: it removes the old leaf (and its hash) from the cache, then writes the new hash directly to the blob without checking whether `new_hash` is already present in a different leaf:

```rust
// blob.rs lines 792–810
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    self.block_status_cache.remove_leaf(&leaf)?;   // removes OLD hash from cache
    leaf.hash.clone_from(new_hash);                // sets NEW hash — no uniqueness check
    leaf.value = value;
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?;
    ...
}
``` [2](#0-1) 

After `remove_leaf` clears the old hash, the cache no longer contains it. If `new_hash` was already present in a different leaf, `insert_entry_to_blob` will overwrite the cache mapping for that hash to point to the upserted leaf's index. The original leaf retains the same hash bytes in the blob but its hash is no longer tracked in `leaf_hash_to_index`. The result is two blob leaves sharing the same hash with only one of them visible to the cache — a state that `insert` is specifically designed to prevent.

The Python binding `py_upsert` exposes this path directly to callers:

```rust
#[pyo3(name = "upsert")]
pub fn py_upsert(&mut self, key: KeyId, value: ValueId, new_hash: Hash) -> PyResult<()> {
    self.upsert(key, value, &new_hash)?;
    Ok(())
}
``` [3](#0-2) 

---

### Impact Explanation

When two leaves share the same hash, the Merkle tree's internal node hashes are computed over duplicate leaf values. This corrupts every ancestor hash up to the root. Concretely:

- **Corrupted tree root**: The root hash no longer faithfully commits to the set of key-value pairs, breaking the DataLayer's integrity guarantee.
- **Forged inclusion proofs**: A `ProofOfInclusion` path constructed for the original leaf (whose hash is no longer in the cache) can be replayed to "prove" inclusion of the upserted key, because both leaves share the same hash bytes. This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

The `upsert` function is reachable from Python via `py_upsert` and from any DataLayer delta-sync code path that applies remote updates. A malicious DataLayer peer that controls the hash field of a delta entry can supply a hash already present in the local tree. Because the hash parameter is accepted as-is (it is not re-derived from the key-value data inside `upsert`), no hash-collision attack is required — the attacker simply replays a hash they observe in the existing tree. The fuzz target `merkle_blob_insert_and_delete.rs` exercises only `insert` and `delete`, not `upsert`, so this path has no existing fuzz coverage. [4](#0-3) 

---

### Recommendation

Add the same `HashAlreadyPresent` guard to `upsert` before writing the new hash:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

+   // Reject if new_hash is already owned by a *different* leaf
+   if self.block_status_cache.contains_leaf_hash(new_hash)
+       && self.block_status_cache.get_index_by_leaf_hash(new_hash)
+           != self.block_status_cache.get_index_by_key(key)
+   {
+       return Err(Error::HashAlreadyPresent());
+   }

    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    ...
}
```

Additionally, extend the fuzz target to exercise `upsert` with hashes drawn from the existing tree to catch this class of bug automatically.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(Vec::new()).unwrap();

let hash_a = Hash(Bytes32::new([0xAA; 32]));
let hash_b = Hash(Bytes32::new([0xBB; 32]));

// Insert two distinct leaves
blob.insert(KeyId(1), ValueId(1), &hash_a, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(2), &hash_b, InsertLocation::Auto {}).unwrap();

// Upsert key 2 with hash_a — the same hash already owned by key 1.
// insert() would return Err(HashAlreadyPresent); upsert() silently succeeds.
blob.upsert(KeyId(2), ValueId(99), &hash_a).unwrap();

// Two leaves now share hash_a; the Merkle root is corrupted.
// check_integrity() will detect the cache/blob divergence.
blob.check_integrity().unwrap_err(); // demonstrates corruption
```

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1435-1440)
```rust
    #[pyo3(name = "upsert")]
    pub fn py_upsert(&mut self, key: KeyId, value: ValueId, new_hash: Hash) -> PyResult<()> {
        self.upsert(key, value, &new_hash)?;

        Ok(())
    }
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_insert_and_delete.rs (L1-54)
```rust
#![no_main]

use libfuzzer_sys::{
    arbitrary::{Arbitrary, Unstructured},
    fuzz_target,
};

use chia_datalayer::{Error, Hash, InsertLocation, KeyId, MerkleBlob, ValueId};

fuzz_target!(|data: &[u8]| {
    let mut blob = MerkleBlob::new(Vec::new()).unwrap();
    blob.check_integrity_on_drop = false;

    let mut unstructured = Unstructured::new(data);
    while !unstructured.is_empty() {
        if unstructured.ratio(8, 10).unwrap() {
            let key = KeyId::arbitrary(&mut unstructured).unwrap();
            let value = ValueId::arbitrary(&mut unstructured).unwrap();
            let hash = Hash::arbitrary(&mut unstructured).unwrap();

            match blob.insert(key, value, &hash, InsertLocation::Auto {}) {
                Ok(_) => {}
                // should remain valid through these errors
                Err(Error::KeyAlreadyPresent()) => continue,
                Err(Error::HashAlreadyPresent()) => continue,
                // other errors should not be occurring
                Err(error) => panic!("unexpected error while inserting: {:?}", error),
            };
        } else {
            let key = if unstructured.ratio(1, 10).unwrap() {
                KeyId::arbitrary(&mut unstructured).unwrap()
            } else {
                let keys_values = blob.get_keys_values().unwrap();
                let keys: Vec<&KeyId> = keys_values.keys().collect();
                let index = match unstructured.choose_index(keys.len()) {
                    Ok(index) => index,
                    Err(_) => continue,
                };
                **keys.get(index).unwrap()
            };
            match blob.delete(key) {
                Ok(_) => {}
                // should remain valid through these errors
                Err(Error::UnknownKey(_)) => continue,
                // other errors should not be occurring
                Err(error) => panic!("unexpected error while deleting: {:?}", error),
            }
        }
    }

    blob.check_integrity().unwrap();
});


```
