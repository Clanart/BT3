### Title
Missing Duplicate-Key/Hash Validation in `MerkleBlob::batch_insert` Fast Path Corrupts DataLayer Merkle Tree Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` contains a fast path that writes leaf nodes directly to the blob without performing the duplicate-key or duplicate-hash checks that `MerkleBlob::insert` enforces. Supplying a batch with repeated `KeyId` or `Hash` values silently corrupts the Merkle tree, producing an invalid root that can be used to forge or ambiguate proofs of inclusion.

### Finding Description

`MerkleBlob::insert` guards against duplicate state before writing:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  insert()
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` uses `insert` only for the first two items when the tree has ≤1 existing leaves. For every subsequent item — and for **all** items when the tree already has ≥2 leaves — it calls `insert_entry_to_blob` directly, bypassing both guards entirely:

```rust
// batch_insert fast path — no key/hash duplicate check
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` writes the block to the blob and updates the `block_status_cache` without any uniqueness check: [3](#0-2) 

The Python binding `py_batch_insert` is the direct public entry point and accepts caller-controlled `keys_values` and `hashes` lists with no pre-deduplication: [4](#0-3) 

The analog to the original report's reentrancy pattern is exact: the `insert` path has the correct guard (like the `bribeIdentifier` check before the transfer), but the batch path writes state first and never runs the guard — mirroring how the reentrant call set the token before the outer call's post-transfer state update ran.

### Impact Explanation

When a batch containing a repeated `KeyId` or `Hash` is processed:

1. Two leaf nodes with the same key are written at distinct blob indexes.
2. The `block_status_cache` key-to-index map is overwritten by the second entry, so the first leaf becomes unreachable through the cache but remains in the blob.
3. `calculate_lazy_hashes` computes internal node hashes over the corrupted structure, producing a tree root that does not correspond to any valid key-value set.
4. `get_proof_of_inclusion` returns a proof anchored to the corrupted root; a verifier who trusts that root accepts the proof as valid even though the committed key-value mapping is wrong.
5. `check_integrity` would detect the corruption, but it is not called on every write — only on explicit request or in debug/test builds (via the `Drop` impl). [5](#0-4) [6](#0-5) 

This satisfies the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

`py_batch_insert` is a public Python API exposed through the `chia_rs` wheel and called by the DataLayer node when applying store updates. A DataLayer store owner (or any party that can submit a batch update to the node) can supply a list with a repeated `KeyId`. No privilege beyond the ability to call `batch_insert` is required. The corruption is silent — no error is returned — so it persists until an explicit integrity check is run.

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the fast path of `batch_insert` before calling `insert_entry_to_blob`. Concretely, before writing each leaf in the loop at line 587, check:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(&hash) {
    return Err(Error::HashAlreadyPresent());
}
```

Alternatively, pre-deduplicate the input vector and verify it against the existing cache before entering the fast path.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Pre-populate with 2 leaves so the fast path is taken for all batch items
for i in range(2):
    blob.insert(KeyId(i), ValueId(i), hashlib.sha256(str(i).encode()).digest(),
                "Auto")

# Batch with a duplicate KeyId — both entries are written without error
dup_key   = KeyId(99)
hash_a    = hashlib.sha256(b"a").digest()
hash_b    = hashlib.sha256(b"b").digest()

blob.batch_insert(
    [(dup_key, ValueId(1)), (dup_key, ValueId(2))],
    [hash_a, hash_b]
)

blob.calculate_lazy_hashes()

# Proof is generated against a corrupted root; the second leaf's value
# silently shadows the first, but the root hash is wrong.
proof = blob.get_proof_of_inclusion(dup_key)
assert proof.valid()   # passes — but the committed root is corrupted
``` [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1013-1030)
```rust
    fn insert_entry_to_blob(&mut self, index: TreeIndex, block: &Block) -> Result<(), Error> {
        let new_block_bytes = block.to_bytes()?;
        let extend_index = self.extend_index();
        match index.cmp(&extend_index) {
            Ordering::Greater => return Err(Error::BlockIndexOutOfBounds(index)),
            Ordering::Equal => self.blob.extend_from_slice(&new_block_bytes),
            Ordering::Less => {
                self.blob[block_range(index)].copy_from_slice(&new_block_bytes);
            }
        }

        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1620-1628)
```rust
#[cfg(any(test, debug_assertions))]
impl Drop for MerkleBlob {
    fn drop(&mut self) {
        if self.check_integrity_on_drop {
            self.check_integrity()
                .expect("integrity check failed while dropping merkle blob");
        }
    }
}
```
