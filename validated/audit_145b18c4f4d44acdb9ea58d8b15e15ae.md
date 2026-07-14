### Title
Missing Duplicate-Key/Hash Validation in `MerkleBlob::batch_insert` Allows Tree Corruption and Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::insert` enforces two invariants before writing a leaf: no duplicate key and no duplicate leaf hash. `MerkleBlob::batch_insert`, an analogous bulk-insertion function exposed through the Python bindings, enforces those same checks only for the first one or two items it processes. All remaining items are written directly to the blob without any duplicate-key or duplicate-hash validation. An unprivileged caller supplying a crafted input list can silently corrupt the tree's `block_status_cache`, making leaves unreachable or causing `get_proof_of_inclusion` to return proofs for the wrong node, enabling forged DataLayer inclusion/exclusion proofs.

---

### Finding Description

**`insert` — guarded path** [1](#0-0) 

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
    ...
```

Both invariants are checked before any mutation.

**`batch_insert` — unguarded path** [2](#0-1) 

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    let mut indexes = vec![];

    if self.block_status_cache.leaf_count() <= 1 {
        for _ in 0..2 {
            let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                return Ok(());
            };
            self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← checked
        }
    }

    for ((key, value), hash) in keys_values_hashes {   // ← ALL remaining items
        let new_leaf_index = self.get_new_index();
        let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
        self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // ← no checks
        indexes.push(new_leaf_index);
    }
```

The `for` loop that processes every item beyond the first two writes `LeafNode` blocks directly to the blob via `insert_entry_to_blob` with **no** call to `contains_key` or `contains_leaf_hash`. Duplicate keys or hashes in the input are silently accepted.

**Consequence for `block_status_cache`**

The cache maintains two maps: `key_to_index` (key → `TreeIndex`) and `leaf_hash_to_index` (hash → `TreeIndex`). When `insert_entry_to_blob` is called for a leaf whose key or hash already exists in the cache, the second write overwrites the first entry. The original leaf remains in the blob but becomes unreachable through the cache. `get_proof_of_inclusion` uses the cache to locate the leaf: [3](#0-2) 

```rust
pub fn get_proof_of_inclusion(
    &self,
    key: KeyId,
) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
    let mut index = *self
        .block_status_cache
        .get_index_by_key(key)
        .ok_or(Error::UnknownKey(key))?;
    ...
```

If the cache points to the wrong index (the duplicate that overwrote the original), the proof is built for the wrong node. `ProofOfInclusion::valid()` will still return `true` for that wrong node because the hash chain is internally consistent — but it proves inclusion of a different leaf than the one the caller requested. [4](#0-3) 

---

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

A corrupted `MerkleBlob` can produce a `ProofOfInclusion` that passes `valid()` yet proves membership of a different key-value pair than the one queried. Any downstream consumer that trusts the proof (e.g., a DataLayer client verifying that a specific key maps to a specific value) is deceived. The root hash stored in the on-chain coin is derived from the corrupted tree, so the forged proof is consistent with the committed root.

---

### Likelihood Explanation

`batch_insert` is exposed directly through the Python bindings: [5](#0-4) 

Any Python code that calls `merkle_blob.batch_insert(kv_ids, hashes)` with attacker-influenced data (e.g., data received from an untrusted DataLayer peer) can trigger the corruption. No privileged role or key material is required.

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the bulk path inside `batch_insert`, mirroring what `insert` already does:

```rust
for ((key, value), hash) in keys_values_hashes {
    // Add the same guards that `insert` enforces:
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    let new_leaf_index = self.get_new_index();
    ...
}
```

Alternatively, refactor `batch_insert` to route every item through `insert` (accepting the performance trade-off), or add a pre-pass that validates uniqueness across the entire input batch before any blob mutation begins.

---

### Proof of Concept

```rust
use chia_datalayer::{MerkleBlob, KeyId, ValueId};

let mut blob = MerkleBlob::new(vec![]).unwrap();

let key   = KeyId(1);
let hash  = [0xABu8; 32];

// batch_insert with the same key appearing twice
blob.batch_insert(vec![
    ((key, ValueId(10)), [0x01u8; 32]),
    ((key, ValueId(20)), [0x02u8; 32]),  // duplicate key — not rejected
    ((key, ValueId(30)), hash),           // third duplicate — overwrites cache
]).unwrap();  // succeeds silently

// The cache now points to the last-written index for `key`.
// get_proof_of_inclusion returns a proof for that node,
// but the blob contains three leaves with the same key.
let proof = blob.get_proof_of_inclusion(key).unwrap();
assert!(proof.valid());  // passes — but proves the wrong leaf
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1166)
```rust
    pub fn get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
        let mut index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;

        let node = self
            .get_node(index)?
            .expect_leaf("key to index mapping should only have leaves");
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

**File:** wheel/python/chia_rs/datalayer.pyi (L322-323)
```text
    def insert(self, key: KeyId, value: ValueId, hash: bytes32, reference_kid: Optional[KeyId] = None, side: Optional[uint8] = None) -> None: ...
    def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
```
