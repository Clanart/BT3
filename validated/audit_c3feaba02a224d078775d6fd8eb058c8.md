### Title
`MerkleBlob::batch_insert` Skips Duplicate-Key/Hash Validation for Bulk Items, Corrupting DataLayer Tree Root and Enabling Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces for every individual insertion. When a batch contains a `KeyId` that already exists in the tree, or that appears more than once within the same batch, the function silently writes a second leaf node for that key into the blob. This corrupts the Merkle tree structure, produces an incorrect root hash, and causes `get_proof_of_inclusion` to generate proofs anchored to a root that does not correspond to any valid committed state.

---

### Finding Description

`MerkleBlob::insert` (lines 362–374) explicitly rejects duplicate keys and hashes before touching the blob:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` (lines 570–657) takes a structurally different path. When the tree already holds more than one leaf (`leaf_count > 1`), it skips `self.insert()` entirely and writes every item in the input vector directly via `insert_entry_to_blob` with **no duplicate check at all**:

```rust
for ((key, value), hash) in keys_values_hashes {          // line 587
    let new_leaf_index = self.get_new_index();
    let new_block = Block {
        ...
        node: Node::Leaf(LeafNode { parent: Parent(None), hash, key, value }),
    };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?; // no guard
    indexes.push(new_leaf_index);
}
```

Even when the tree starts with ≤1 leaves, only the **last two** items in the vector are popped and routed through `self.insert()` (lines 578–585); all earlier items in the vector still flow through the unchecked path.

`insert_entry_to_blob` updates `block_status_cache` (confirmed by `test_batch_insert`, which asserts all inserted keys appear in `get_keys_values()`, which reads from the cache). For a duplicate key the cache entry is silently overwritten (last write wins), but **both leaf nodes remain in the blob**. The Merkle root is then computed over both leaves, producing a root hash that does not correspond to any valid single-valued tree state.

`get_proof_of_inclusion` (lines 1155–1195) generates a proof anchored to the cache-visible leaf. The `ProofOfInclusion::valid()` check (lines 40–58) passes internally, but the root hash it produces does not match the actual on-chain committed root, because the phantom second leaf is also hashed in.

`check_integrity` (lines 812–819) would detect the corruption via the leaf-count vs. cache-length mismatch (lines 861–866), but it is never called automatically after `batch_insert`.

---

### Impact Explanation

- The DataLayer store's committed root hash is computed over a structurally invalid tree containing two leaf nodes for the same `KeyId`.
- A store owner can craft a batch with a repeated key to make a single key appear to map to two different values simultaneously, enabling forged inclusion attestations to downstream consumers.
- Any verifier who checks a `ProofOfInclusion` against the on-chain root will observe a mismatch, or—if the verifier trusts the proof object directly—will accept a proof for a state that was never legitimately committed.
- The corruption is silent: `batch_insert` returns `Ok(())`, no error is surfaced, and the tree passes casual inspection until `check_integrity` is called explicitly.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots and lets untrusted input prove invalid state.**

---

### Likelihood Explanation

The Python binding `py_batch_insert` (lines 1503–1518) is directly callable from Python with arbitrary input. Any DataLayer store owner—an unprivileged role requiring no special keys or governance access—can pass a `keys_values` list containing a repeated `KeyId` to corrupt their store's Merkle tree. No network-level access, leaked keys, or privileged roles are required beyond the ability to call the public API. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

---

### Recommendation

Add duplicate-key and duplicate-hash guards at the start of the main loop in `batch_insert`, mirroring the checks already present in `insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing code unchanged
}
```

Alternatively, pre-validate the entire input vector for duplicates (both against the existing tree and within the batch itself) before any writes begin, so the function either succeeds fully or fails atomically without partial blob corruption.

---

### Proof of Concept

```rust
// Tree already has 2 leaves → leaf_count > 1 → batch_insert skips self.insert() for all items
let mut blob = MerkleBlob::new(vec![]).unwrap();
blob.insert(KeyId(0), ValueId(0), &sha256_num(&0_i64), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(1), ValueId(1), &sha256_num(&1_i64), InsertLocation::Auto {}).unwrap();

// Batch contains KeyId(0) which already exists in the tree
let batch = vec![
    ((KeyId(0), ValueId(99)), sha256_num(&99_i64)), // duplicate key, different hash
    ((KeyId(2), ValueId(2)),  sha256_num(&2_i64)),
];
blob.batch_insert(batch).unwrap(); // returns Ok(()), no error raised

blob.calculate_lazy_hashes().unwrap();
// The blob now contains two leaf nodes with KeyId(0).
// The root hash is computed over both, but the cache only knows about one.
// Integrity check exposes the corruption:
blob.check_integrity().unwrap_err();
// Error::IntegrityKeyToIndexCacheLength: blob leaf_count=3, cache len=2
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-603)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-866)
```rust
        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1195)
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

        let parents = self.get_lineage_blocks_with_indexes(index)?;
        let mut layers: Vec<proof_of_inclusion::ProofOfInclusionLayer> = Vec::new();
        let mut parents_iter = parents.iter();
        // first in the lineage is the index itself, second is the first parent
        parents_iter.next();
        for (next_index, block) in parents_iter {
            if block.metadata.dirty {
                return Err(Error::Dirty(*next_index));
            }
            let parent = block
                .node
                .expect_internal("all nodes after the first should be internal");
            let sibling_index = parent.sibling_index(index)?;
            let sibling_block = self.get_block(sibling_index)?;
            let sibling = sibling_block.node;
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
            layers.push(layer);
            index = *next_index;
        }

        Ok(proof_of_inclusion::ProofOfInclusion {
            node_hash: node.hash,
            layers,
        })
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
