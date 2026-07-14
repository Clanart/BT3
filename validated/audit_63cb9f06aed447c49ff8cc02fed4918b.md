### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key Guard, Corrupting Tree Root and Invalidating Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash checks that `MerkleBlob::insert` enforces for every item beyond the first two. An unprivileged caller who supplies a batch containing a key that already exists in the tree (or a duplicate key within the batch itself) silently corrupts the `BlockStatusCache`, leaves orphaned leaf nodes in the blob, and produces a tree whose computed root hash no longer matches its actual structure. Any subsequent `get_proof_of_inclusion` call for the affected key returns a structurally invalid proof.

### Finding Description

`MerkleBlob::insert` guards against duplicate keys and hashes at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`MerkleBlob::batch_insert` uses `insert` only for the first two items when the tree has ≤ 1 existing leaf. All remaining items — and **all** items when the tree already has ≥ 2 leaves — are written directly via `insert_entry_to_blob` with no existence check:

```rust
for ((key, value), hash) in keys_values_hashes {   // no duplicate check
    let new_leaf_index = self.get_new_index();
    let new_block = Block {
        node: Node::Leaf(LeafNode {
            parent: Parent(None),   // disconnected from tree
            hash, key, value,
        }),
        ...
    };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` calls `BlockStatusCache::add_leaf`, which unconditionally overwrites the cache maps:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silently overwrites
    self.leaf_hash_to_index.insert(leaf.hash, index); // silently overwrites
}
```

When a duplicate key is processed:

1. The cache's `key_to_index` is updated to point to the **new** (disconnected) leaf index.
2. The **old** leaf remains physically in the blob, still wired into the tree structure via its parent pointer.
3. The new leaf has `parent: Parent(None)` — it is not yet attached to the tree.
4. After the loop, `insert_subtree_at_key` attaches the batch's subtree to the existing tree, but the orphaned old leaf is still reachable from the tree root.

The result is a split state: the cache says the key lives at the new index (which has no parent), while the tree structure still contains the old leaf at its original position. The root hash computed by `calculate_lazy_hashes` reflects the old leaf; `get_proof_of_inclusion` navigates from the cache's (wrong) index and produces a proof with an empty or mismatched layer chain.

The Python binding `py_batch_insert` exposes this path directly to any caller:

```rust
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

### Impact Explanation

A caller who supplies a duplicate key in a `batch_insert` call corrupts the DataLayer Merkle tree's root hash and its proof-of-inclusion output. The stored root no longer reflects the actual leaf set. Proofs generated from the corrupted tree can assert inclusion of a key at a hash that does not match the committed root, or fail to prove inclusion of a key that is genuinely present. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

The Python binding `py_batch_insert` is a public API. Any DataLayer client that constructs a batch with a repeated key — whether by mistake or by deliberate crafting — triggers the corruption. No privileged role is required. The `insert` path correctly rejects duplicates, so the discrepancy is non-obvious and unlikely to be caught by callers who assume `batch_insert` has the same safety guarantees.

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the fast path inside `batch_insert` before calling `insert_entry_to_blob`:

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

Alternatively, deduplicate the input vector before processing and return an error on any collision, consistent with the behaviour of `insert`.

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn main() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Seed the tree with >= 2 leaves so batch_insert skips the safe path entirely
    for i in 0i64..3 {
        blob.insert(
            KeyId(i), ValueId(i),
            &Hash(Bytes32::new([i as u8; 32])),
            InsertLocation::Auto {},
        ).unwrap();
    }

    // batch_insert with a key that already exists (KeyId(0))
    let dup_key = KeyId(0);
    let new_value = ValueId(999);
    let new_hash = Hash(Bytes32::new([0xAB; 32]));

    // This succeeds — no error is raised despite the duplicate
    blob.batch_insert(vec![((dup_key, new_value), new_hash)]).unwrap();

    blob.calculate_lazy_hashes().unwrap();

    // check_integrity detects the corruption
    blob.check_integrity().unwrap_err(); // fails: leaf count mismatch

    // get_proof_of_inclusion returns a proof whose root_hash() != actual root
    let proof = blob.get_proof_of_inclusion(dup_key).unwrap();
    let tree_root = blob.get_hash(chia_datalayer::TreeIndex(0)).unwrap();
    assert_ne!(proof.root_hash(), tree_root); // proof is invalid
}
```

**Key references:**

- `batch_insert` fast path (no duplicate check): [1](#0-0) 
- `insert` duplicate guards (absent in `batch_insert`): [2](#0-1) 
- `add_leaf` silently overwrites cache: [3](#0-2) 
- `insert_entry_to_blob` calls `add_leaf` unconditionally: [4](#0-3) 
- Python binding exposing `batch_insert`: [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }
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
