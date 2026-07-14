### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Validation, Corrupting DataLayer Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the `KeyAlreadyPresent` and `HashAlreadyPresent` guards that the single-item `insert` path enforces. An unprivileged caller can supply a batch containing duplicate `KeyId` or `Hash` values and silently write structurally corrupt leaf nodes into the blob, producing an incorrect Merkle root. Any `ProofOfInclusion` derived from that root is then untrustworthy.

### Finding Description

`MerkleBlob::insert` opens with two mandatory guards:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` takes a completely different code path. When the tree already holds two or more leaves (`leaf_count > 1`), the early-exit branch that calls `self.insert()` is never entered, and every item in the batch is written directly via `insert_entry_to_blob` with no duplicate check at all:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    let mut indexes = vec![];

    if self.block_status_cache.leaf_count() <= 1 {
        for _ in 0..2 {
            ...
            self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← guarded
        }
    }

    for ((key, value), hash) in keys_values_hashes {   // ← NO guard here
        let new_leaf_index = self.get_new_index();
        let new_block = Block { ... Node::Leaf(LeafNode { key, value, hash, ... }) };
        self.insert_entry_to_blob(new_leaf_index, &new_block)?;
        indexes.push(new_leaf_index);
    }
``` [2](#0-1) 

Even when `leaf_count <= 1`, only the last two items (popped from the end of the vector) pass through `insert`. All preceding items in a batch of three or more still reach the unguarded loop.

The `block_status_cache` maps `KeyId → TreeIndex` and `Hash → TreeIndex`. Writing a second leaf with an already-present key overwrites or duplicates the cache entry, making the cache inconsistent with the blob. Internal-node hashes are then computed over a tree that contains phantom or duplicated leaves, yielding a wrong root hash.

`get_proof_of_inclusion` trusts the cache to locate the leaf and walks the lineage to build `ProofOfInclusion`: [3](#0-2) 

`ProofOfInclusion::valid()` re-derives the root by hashing up the stored `combined_hash` chain: [4](#0-3) 

Because the stored `combined_hash` values were computed over the corrupt tree, a proof generated after a duplicate-key batch insert will call `valid()` → `true` against a root that does not represent the actual key-value set.

The Python binding exposes `batch_insert` directly to callers: [5](#0-4) 

### Impact Explanation

An attacker who controls the input to `batch_insert` (e.g., via the Python API or any higher-level DataLayer operation that calls it) can:

1. Insert a duplicate `KeyId` with a different `ValueId` and `Hash`, silently overwriting the cache entry for the original key.
2. Force `calculate_lazy_hashes` to propagate hashes over the corrupt structure, producing a wrong root.
3. Obtain a `ProofOfInclusion` that passes `valid()` but proves membership of a key-value pair that was never legitimately committed, or that proves a stale/overwritten value as current.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

`batch_insert` is the standard bulk-insertion API and is called in production DataLayer workflows (e.g., `test_proof_of_inclusion_merkle_blob` in `tests/test_datalayer.py` uses it for all large inserts). Any caller that passes a list containing a repeated `KeyId` — whether by mistake or maliciously — triggers the corruption silently, with no error returned. The tree passes `check_integrity` only if the cache and blob are consistent; a duplicate key that overwrites the cache entry will appear consistent to the integrity checker while the blob still holds the orphaned duplicate leaf.

### Recommendation

Add the same duplicate-key and duplicate-hash guards at the top of `batch_insert` (or inside the unguarded loop) that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing direct-insert path
}
```

Alternatively, collect all keys and hashes from the batch up front and validate uniqueness before any blob writes begin.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 leaves so leaf_count > 1 and the guarded branch is skipped
blob.insert(KeyId(1), ValueId(100), h(1))
blob.insert(KeyId(2), ValueId(200), h(2))

# batch_insert with a duplicate KeyId(1) — third item bypasses KeyAlreadyPresent check
blob.batch_insert(
    [(KeyId(3), ValueId(300)), (KeyId(4), ValueId(400)), (KeyId(1), ValueId(999))],
    [h(3), h(4), h(0xDEAD)],   # h(0xDEAD) ≠ h(1), so HashAlreadyPresent also bypassed
)

blob.calculate_lazy_hashes()

# Proof for KeyId(1) now reflects the corrupt tree root
proof = blob.get_proof_of_inclusion(KeyId(1))
assert proof.valid()   # passes — but root is wrong

# The original value 100 is gone; the tree silently accepted value 999
kv = blob.get_keys_values()
print(kv[KeyId(1)])   # 999, not 100 — silent overwrite with no error
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1196)
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
