### Title
`batch_insert()` Bypasses Duplicate Key/Hash Validation for Items Beyond the First Two — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert()` applies the duplicate-key and duplicate-hash guards only to the first ≤ 2 items in the batch (via the `insert()` path), then silently bypasses those same guards for every subsequent item by calling `insert_entry_to_blob()` directly. An attacker-controlled batch containing a repeated `KeyId` or repeated leaf `Hash` will corrupt the Merkle tree without triggering an error, producing an incorrect root and enabling forged inclusion proofs.

### Finding Description

`MerkleBlob::insert()` enforces two invariants before writing a leaf:

```rust
// blob.rs:369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert()` calls `insert()` (with those guards) only for the bootstrap case where the tree currently holds ≤ 1 leaf:

```rust
// blob.rs:578-585
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
```

All remaining items in the batch are written through `insert_entry_to_blob()` directly, with **no duplicate check**:

```rust
// blob.rs:587-603
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

The subsequent subtree-linking phase (`insert_subtree_at_key`, line 653) then attaches all of these unchecked leaves to the live tree and recomputes internal hashes upward, permanently embedding the duplicate into the committed root.

### Impact Explanation

- Two leaf nodes sharing the same `KeyId` exist in the blob. The `block_status_cache` maps a key to exactly one index, so one of the two leaves becomes unreachable through the cache while still contributing to internal-node hashes.
- The computed root hash is wrong relative to the intended key-value set.
- A `get_proof_of_inclusion` call for the duplicated key returns a proof anchored to whichever leaf the cache points to; the other leaf's hash silently inflates internal nodes, making the proof verify against a root that does not represent the true state.
- This satisfies the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is a `pub` method on `MerkleBlob`, which is exposed to Python via the `py-bindings` feature. Any Python caller (including DataLayer store update logic) that passes a `keys_values_hashes` list containing a repeated key or hash — whether by mistake or by adversarial construction — will silently corrupt the tree. No privileged role is required; the entry path is ordinary DataLayer write operations.

### Recommendation

Apply the same duplicate-key and duplicate-hash checks inside the main batch loop, either by routing every item through `insert()` or by performing an explicit pre-pass that rejects duplicates before any writes occur:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing direct-write path ...
}
```

Alternatively, validate the entire input vector for uniqueness before the function begins writing anything, so the tree is never left in a partially-written corrupt state.

### Proof of Concept

```
Initial tree: empty (leaf_count == 0)

batch_insert([
    ((key=A, val=1), hash_A),   // item 1 – popped first, goes through insert() ✓
    ((key=B, val=2), hash_B),   // item 2 – popped second, goes through insert() ✓
    ((key=A, val=3), hash_A2),  // item 3 – goes through insert_entry_to_blob() directly
                                //           NO contains_key(A) check → duplicate written
])

Result:
  - Two leaf nodes with key=A exist in the blob.
  - block_status_cache.key_to_index[A] points to only one of them.
  - Internal hashes above the duplicate leaf are computed from both,
    producing a root that does not match any canonical key-value set.
  - get_proof_of_inclusion(A) returns a proof that verifies against
    this corrupted root, enabling forged state attestation.
```

**Relevant code locations:**

`insert()` duplicate guards: [1](#0-0) 

`batch_insert()` guarded bootstrap path (first ≤ 2 items): [2](#0-1) 

`batch_insert()` unguarded main loop (items 3+): [3](#0-2) 

Subtree attachment that commits the corrupted leaves to the live root: [4](#0-3)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L650-654)
```rust
        if indexes.len() == 1 {
            // OPT: can we avoid this extra min height leaf traversal?
            let min_height_leaf = self.get_min_height_leaf()?;
            self.insert_subtree_at_key(min_height_leaf.key, indexes[0], Side::Left)?;
        }
```
