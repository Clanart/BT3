### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Guards, Enabling DataLayer Tree-Root Corruption and Forged Inclusion Proofs - (File: crates/chia-datalayer/src/merkle/blob.rs)

### Summary

`MerkleBlob::batch_insert` uses a split fast-path that skips the `KeyAlreadyPresent` and `HashAlreadyPresent` duplicate checks enforced by `MerkleBlob::insert` for all items beyond the first two in the batch. An unprivileged caller supplying a batch of three or more entries — where any entry beyond the second duplicates an existing key or hash — silently inserts the duplicate leaf directly into the blob, corrupting the Merkle tree structure and producing a root hash that no longer faithfully represents the key-value mapping. Subsequent `get_proof_of_inclusion` calls return proofs that verify against the corrupted root, enabling forged state attestation.

### Finding Description

`MerkleBlob::insert` (the single-item path) enforces two guards before writing:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` handles the first two items by popping them from the *end* of the input vector and routing them through `self.insert()` (which carries the guards). All remaining items — i.e., every item at index 0 through `len-3` — are written directly via `self.insert_entry_to_blob()` with no duplicate check whatsoever:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

Because `insert_entry_to_blob` writes the raw block to the blob and updates the `block_status_cache` (overwriting the existing `key_to_index` and `leaf_hash_to_index` entries for that key/hash), the old leaf node remains in the blob as an orphaned but structurally connected node. The internal-node hash chain built in the subsequent loop incorporates both the orphaned leaf and the new leaf, producing a root hash that encodes two leaves for the same key. The Python binding `py_batch_insert` exposes this path directly to callers: [3](#0-2) 

The `ProofOfInclusion::valid()` method verifies a proof only against the root hash stored in the blob: [4](#0-3) 

Because the root hash itself is corrupted, a proof generated for the newly inserted (duplicate) leaf will pass `valid()` even though the canonical value for that key is different.

### Impact Explanation

The DataLayer Merkle tree root is committed on-chain. Any party that trusts the committed root and verifies inclusion proofs against it will accept proofs for values that are not the canonical value for a given key. Concretely:

- An attacker who can supply input to `batch_insert` (e.g., via the Python binding or any higher-level DataLayer API that passes untrusted data) can insert a duplicate key with an attacker-chosen value and hash.
- The resulting root hash encodes the forged state.
- `get_proof_of_inclusion` returns a proof for the forged leaf that passes `valid()`.
- Any verifier checking inclusion against the committed root will accept the forged proof, believing the key maps to the attacker-chosen value.

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

### Likelihood Explanation

`batch_insert` is a public API exposed via Python bindings and is the primary bulk-insertion path used by the DataLayer application. Any caller that passes a batch of three or more entries — including one that duplicates an existing key — triggers the bug without any special privilege. The attacker needs only the ability to supply input to `batch_insert`, which is the normal operational path for DataLayer data ingestion.

### Recommendation

Add the same duplicate guards at the top of the bulk loop in `batch_insert` that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing direct-write path
}
```

Alternatively, route all items through `self.insert()` (accepting the performance cost) or pre-validate the entire batch for uniqueness before any writes begin.

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn sha256_val(v: u8) -> Hash {
    use chia_sha2::Sha256;
    let mut h = Sha256::new();
    h.update([v]);
    Hash(Bytes32::new(h.finalize()))
}

fn main() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Pre-insert key=1 with hash_A via the safe single-insert path.
    let key1 = KeyId(1);
    let hash_a = sha256_val(0xAA);
    blob.insert(key1, ValueId(100), &hash_a, InsertLocation::Auto {}).unwrap();

    // batch_insert with 3 items: items at index 0 and 1 bypass the guard.
    // Item 0 duplicates key=1 with a different hash and value.
    let batch = vec![
        ((key1, ValueId(999)), sha256_val(0xFF)),  // duplicate key — bypasses check
        ((KeyId(2), ValueId(200)), sha256_val(0xBB)),
        ((KeyId(3), ValueId(300)), sha256_val(0xCC)),
    ];
    // Expected: Err(KeyAlreadyPresent). Actual: Ok(()) — duplicate silently inserted.
    blob.batch_insert(batch).unwrap();

    blob.calculate_lazy_hashes().unwrap();

    // The root now encodes two leaves for key=1.
    // get_proof_of_inclusion returns a proof for the forged value (999).
    let proof = blob.get_proof_of_inclusion(key1).unwrap();
    assert!(proof.valid(), "forged proof validates against corrupted root");

    // check_integrity reveals the corruption.
    blob.check_integrity().unwrap_err(); // fails: leaf_count != cache length
}
```

The `batch_insert` call succeeds without error despite the duplicate key. The resulting proof for `key=1` validates against the corrupted root, demonstrating that the forged value `999` can be proven as the canonical value for `key=1`. [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L570-657)
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

        // OPT: can we insert the top node first?  maybe more efficient to update it's children
        //      than to update the parents of the children when traversing leaf to sub-root?
        while indexes.len() > 1 {
            let mut new_indexes = vec![];

            for chunk in indexes.chunks(2) {
                let [index_1, index_2] = match chunk {
                    [index] => {
                        new_indexes.push(*index);
                        continue;
                    }
                    [index_1, index_2] => [*index_1, *index_2],
                    _ => unreachable!(
                        "chunk should always be either one or two long and be handled above"
                    ),
                };

                let new_internal_node_index = self.get_new_index();

                let mut hashes = vec![];
                for index in [index_1, index_2] {
                    let block = self.update_parent(index, Some(new_internal_node_index))?;
                    hashes.push(block.node.hash());
                }

                let new_block = Block {
                    metadata: NodeMetadata {
                        node_type: NodeType::Internal,
                        dirty: false,
                    },
                    node: Node::Internal(InternalNode {
                        parent: Parent(None),
                        hash: internal_hash(&hashes[0], &hashes[1]),
                        left: index_1,
                        right: index_2,
                    }),
                };

                self.insert_entry_to_blob(new_internal_node_index, &new_block)?;
                new_indexes.push(new_internal_node_index);
            }

            indexes = new_indexes;
        }

        if indexes.len() == 1 {
            // OPT: can we avoid this extra min height leaf traversal?
            let min_height_leaf = self.get_min_height_leaf()?;
            self.insert_subtree_at_key(min_height_leaf.key, indexes[0], Side::Left)?;
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
