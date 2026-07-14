The vulnerability claim is concrete and traceable. Let me verify the exact mechanics of `insert_entry_to_blob` and `add_leaf` to confirm the silent overwrite behavior.

The code is already fully read. Here is the analysis:

---

### Title
`batch_insert` Bypasses Duplicate-Key Guard for Non-Bootstrapped Items, Corrupting the Merkle Blob — (`crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` splits its input into two code paths. The first two items (popped from the **end** of the vector) go through `self.insert()`, which checks `KeyAlreadyPresent`. All remaining items (the **front** of the vector) are written directly to the blob via `insert_entry_to_blob` with no duplicate-key check. When a caller supplies a batch containing a repeated `KeyId`, the duplicate is silently accepted, producing two leaf nodes with the same key in the blob and corrupting the `block_status_cache`.

### Finding Description

`batch_insert` at line 578–585 pops the **last** two elements and routes them through the guarded `self.insert()`: [1](#0-0) 

All remaining elements (the **front** of the original vector) are then written directly: [2](#0-1) 

`insert_entry_to_blob` unconditionally calls `block_status_cache.add_leaf()` for every leaf block it writes: [3](#0-2) 

`add_leaf` uses `HashMap::insert`, which **silently overwrites** any existing entry for the same `KeyId`: [4](#0-3) 

**Concrete exploit path** with an initially empty tree and input `[(key_a, val_a, hash_a), (key_b, val_b, hash_b), (key_a, val_c, hash_c)]`:

1. `leaf_count == 0 ≤ 1`, so the bootstrap branch fires.
2. `.pop()` #1 → `(key_a, val_c, hash_c)` → `self.insert(key_a, …)` succeeds; key_a is now in the cache at index I₁.
3. `.pop()` #2 → `(key_b, val_b, hash_b)` → `self.insert(key_b, …)` succeeds.
4. Remaining vector: `[(key_a, val_a, hash_a)]` — processed by the raw loop.
5. `insert_entry_to_blob` writes a second leaf with `key_a` at new index I₂, then calls `add_leaf(I₂, leaf_with_key_a)`.
6. `key_to_index.insert(key_a, I₂)` overwrites the cache; the cache now points to I₂.
7. The blob contains **two** leaf nodes with `key_a`: the original at I₁ (still referenced by its parent internal node) and the duplicate at I₂ (with `parent: Parent(None)`, dangling).

### Impact Explanation

The resulting state violates multiple invariants simultaneously:

- **Cache corruption**: `key_to_index[key_a]` points to I₂, but the tree structure references I₁. `get_proof_of_inclusion(key_a)` follows the cache to I₂, which has no parent, producing an empty/invalid proof lineage.
- **Root hash corruption**: The subtree rooted above I₁ still hashes over the original key_a leaf; the duplicate at I₂ is not part of any subtree, so the root hash does not reflect the "inserted" duplicate.
- **`check_integrity()` failure**: The `ParentFirstIterator` traversal finds the old key_a leaf at I₁ and calls `get_index_by_key(key_a)` → I₂ ≠ I₁, triggering `IntegrityKeyToIndexCacheIndex`. [5](#0-4) 

- **`delete(key_a)` operates on the wrong node**: it uses the cache to find I₂ and removes the dangling duplicate, leaving the original key_a leaf at I₁ permanently orphaned in the blob.

This matches the allowed High impact: *DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.*

### Likelihood Explanation

The Python binding `py_batch_insert` accepts caller-supplied lists with no deduplication: [6](#0-5) 

Any code path that feeds attacker-controlled key-value pairs into `batch_insert` without prior deduplication is exploitable. The DataLayer store accumulates pending operations from external clients; if those operations are batched without a uniqueness pass, the attacker can trigger this with a single malformed batch submission.

### Recommendation

Add a duplicate-key check at the start of `batch_insert` (or inside the raw loop) before writing to the blob. The simplest fix is to check `self.block_status_cache.contains_key(key)` in the raw loop and return `Err(Error::KeyAlreadyPresent())`, mirroring the guard already present in `self.insert()`. Additionally, deduplicate the input vector before processing (e.g., using a `HashSet` of seen keys across both the bootstrap and raw paths).

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn main() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();

    let key_a = KeyId(1);
    let key_b = KeyId(2);
    let hash_a = Hash(Bytes32::new([1u8; 32]));
    let hash_b = Hash(Bytes32::new([2u8; 32]));
    let hash_c = Hash(Bytes32::new([3u8; 32]));

    // Batch with key_a appearing at index 0 AND index 2.
    // .pop() takes index 2 first (key_a/hash_c) → guarded insert.
    // .pop() takes index 1 (key_b/hash_b) → guarded insert.
    // Remaining: index 0 (key_a/hash_a) → raw unguarded write.
    let batch = vec![
        ((key_a, ValueId(10)), hash_a),
        ((key_b, ValueId(20)), hash_b),
        ((key_a, ValueId(30)), hash_c),
    ];

    blob.batch_insert(batch).unwrap(); // succeeds — no error raised

    // Tree is now corrupted: two leaf nodes with key_a in the blob.
    blob.check_integrity().unwrap_err(); // fails with IntegrityKeyToIndexCacheIndex
}
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L840-851)
```rust
                    leaf_count += 1;
                    let cached_index = self
                        .block_status_cache
                        .get_index_by_key(node.key)
                        .ok_or(Error::IntegrityKeyNotInCache(node.key))?;
                    if *cached_index != index {
                        return Err(Error::IntegrityKeyToIndexCacheIndex(
                            node.key,
                            index,
                            *cached_index,
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
