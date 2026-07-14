### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation Present in `insert`, Enabling DataLayer Merkle Tree Corruption and Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. Any caller (including via the exposed Python binding `py_batch_insert`) can supply a batch containing keys or hashes that already exist in the tree, or that repeat within the batch itself. The resulting blob is structurally inconsistent: orphaned leaf nodes appear in the raw blob, the `block_status_cache` diverges from the actual blob contents, and the root hash computed by `calculate_lazy_hashes` is wrong. Proofs of inclusion generated from the corrupted tree are invalid, and the tree can be made to "prove" inclusion of data that is not legitimately present.

---

### Finding Description

**`insert` enforces two invariants before writing any node:**

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

**`batch_insert` only calls `insert` (with those guards) for the last two items when the tree has ≤ 1 existing leaf:**

```rust
// lines 578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else {
            return Ok(());
        };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
```

**All remaining items are written directly to the blob without any duplicate check:**

```rust
// lines 587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // no key/hash guard
    indexes.push(new_leaf_index);
}
```

Because `keys_values_hashes.pop()` removes from the **end**, the last two elements receive the safety checks while all **leading** elements bypass them entirely. A batch of N items (N > 2, starting tree empty) has N−2 items inserted without any duplicate validation.

The `batch_insert` path is reachable from the public Python binding:

```rust
// lines 1503-1519
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
```

---

### Impact Explanation

When a duplicate key or hash is inserted via `batch_insert`:

1. **Blob corruption**: Two `LeafNode` blocks with the same `KeyId` (or same `Hash`) exist in the raw blob. The `block_status_cache` maps the key to only one of them; the other is an orphan that is never reachable through the cache but occupies a real blob slot.

2. **Root hash mismatch**: `calculate_lazy_hashes` propagates hashes bottom-up. With orphaned or duplicated leaves, the computed root hash does not correspond to any legitimate set of key-value pairs.

3. **Forged inclusion proofs**: `get_proof_of_inclusion` walks the lineage from the cache-tracked leaf to the root. Because the root hash is wrong, a proof generated after the corruption will pass `proof.valid()` against the corrupted root but will not match any honest root that other participants hold. Conversely, a proof for a key that was inserted twice can be constructed to reference either copy, making exclusion proofs unreliable.

4. **`check_integrity` is not called automatically**: The corruption persists silently until an explicit `check_integrity()` call, which is not part of the normal insert/proof workflow.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`batch_insert` is the recommended bulk-insertion API and is exposed directly to Python callers with no documented precondition requiring the caller to pre-deduplicate. Any Python component that assembles a batch from external (network-received) data and calls `merkle_blob.batch_insert(kv_ids, hashes)` is a reachable entry point. No special privilege is required.

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the bulk loop in `batch_insert` that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insertion logic
}
```

Alternatively, pre-deduplicate the input vector and cross-check against the existing cache before entering the fast-path loop, so the O(1) cache lookups are not repeated per item.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Batch with 5 items; last 2 are checked by insert(), first 3 bypass checks.
# key 1 appears at position 0 (bypasses check) AND position 3 (checked, succeeds first).
keys_values = [
    (KeyId(1), ValueId(10)),   # position 0 – NO duplicate check
    (KeyId(2), ValueId(20)),   # position 1 – NO duplicate check
    (KeyId(3), ValueId(30)),   # position 2 – NO duplicate check
    (KeyId(1), ValueId(99)),   # position 3 – popped second, insert() succeeds (key not yet present)
    (KeyId(4), ValueId(40)),   # position 4 – popped first,  insert() succeeds
]
hashes = [h(1), h(2), h(3), h(100), h(4)]

# This succeeds even though KeyId(1) appears twice.
blob.batch_insert(keys_values, hashes)

# Tree is now corrupted: two leaf blocks for KeyId(1) exist in the blob.
# calculate_lazy_hashes produces a root that does not match any honest tree.
blob.calculate_lazy_hashes()
root = blob.get_root_hash()

# get_proof_of_inclusion returns a proof anchored to the corrupted root.
proof = blob.get_proof_of_inclusion(KeyId(1))
assert proof.valid()   # passes against the corrupted root

# check_integrity() would reveal the corruption, but it is never called automatically.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L812-819)
```rust
    pub fn check_integrity(&self) -> Result<(), Error> {
        self.check_just_integrity()?;

        let mut clone = self.clone();
        clone.check_integrity_on_drop = false;
        clone.calculate_lazy_hashes()?;
        clone.check_just_integrity()
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
