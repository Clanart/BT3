### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Validation, Corrupting DataLayer Tree Root and Proof State - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `insert` enforces for all items beyond the first two in a batch. Any caller — including the Python-binding entry point `py_batch_insert` — can supply a list containing a repeated `KeyId` or a `KeyId` already present in the tree, silently corrupting the `block_status_cache`, producing a root hash that is inconsistent with what can be proven, and breaking subsequent proof-of-inclusion generation.

### Finding Description

`MerkleBlob::insert` guards against duplicate keys and hashes before writing to the blob:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` only for the first two items (when the tree has ≤ 1 existing leaf). Every subsequent item is written directly via `insert_entry_to_blob` with no duplicate check:

```rust
// lines 587-602
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // no guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which performs a plain `HashMap::insert` — silently overwriting any prior mapping for the same key or hash:

```rust
// lines 1024-1026
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    ...
}
``` [3](#0-2) 

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);        // overwrites silently
    self.leaf_hash_to_index.insert(leaf.hash, index); // overwrites silently
}
``` [4](#0-3) 

The Python-binding entry point performs only a length-match check before forwarding to `batch_insert`:

```rust
// lines 1503-1518
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    if keys_values.len() != hashes.len() { ... }
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

### Impact Explanation

When a duplicate `KeyId` appears at position ≥ 3 in the batch (or when a batch key already exists in the tree):

1. Both leaf nodes are written into the raw blob at distinct indexes.
2. `add_leaf` overwrites `key_to_index` so the cache points only to the later index; the earlier index is never added to `free_indexes` — it is a "ghost" leaf permanently embedded in the blob.
3. The internal-node hashes computed during the batch-insert tree-building phase incorporate both leaf hashes, so the final root hash reflects a tree that contains the ghost leaf.
4. `get_proof_of_inclusion` uses the cache to locate the leaf; it can only find the later duplicate, so no valid proof can be generated for the ghost leaf's position in the tree.
5. `check_integrity` would detect the mismatch (`leaf_count != key_to_index_cache_length`), but it is not called automatically after `batch_insert`.

The result is a committed root hash that is inconsistent with the provable state of the tree — a corrupted DataLayer Merkle root. [6](#0-5) 

### Likelihood Explanation

The Python binding `MerkleBlob.batch_insert` is a public API surface reachable by any DataLayer client without any privilege. No hash collision is required — only a repeated integer `KeyId`. The condition is triggered whenever a caller supplies ≥ 3 items and any two share a `KeyId`, or when any item in position ≥ 3 duplicates a key already in the tree. The existing test suite (`test_batch_insert`) only exercises non-overlapping key sets, so the defect is untested. [7](#0-6) 

### Recommendation

Add duplicate-key and duplicate-hash guards at the top of `batch_insert` (or inside the fast-path loop) mirroring the checks in `insert`:

```rust
for ((key, value), hash) in &keys_values_hashes {
    if self.block_status_cache.contains_key(*key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(hash) {
        return Err(Error::HashAlreadyPresent());
    }
}
```

Alternatively, accumulate inserted keys/hashes in a local `HashSet` during the loop and check each new entry against it before calling `insert_entry_to_blob`. Add a regression test that calls `batch_insert` with a duplicate key and asserts the error, then verifies `check_integrity` passes on the unchanged tree.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(blob=bytearray())

# Pre-populate with 2 leaves so batch_insert skips the guarded insert() path
k0, v0 = KeyId(0), ValueId(0)
k1, v1 = KeyId(1), ValueId(1)
h0 = hashlib.sha256(b"h0").digest()
h1 = hashlib.sha256(b"h1").digest()
blob.insert(k0, v0, h0)
blob.insert(k1, v1, h1)

# Now batch_insert with 3 items; item 0 (processed last in the fast path)
# is a duplicate of k0 already in the tree.
# Items beyond the first 2 go through insert_entry_to_blob with no guard.
dup_key = KeyId(0)          # already present
dup_hash = hashlib.sha256(b"dup").digest()
k2, h2 = KeyId(2), hashlib.sha256(b"h2").digest()
k3, h3 = KeyId(3), hashlib.sha256(b"h3").digest()

# batch_insert pops from the end for the first 2 guarded calls,
# then iterates the remainder without checks.
blob.batch_insert(
    [(k2, ValueId(2)), (k3, ValueId(3)), (dup_key, ValueId(99))],
    [h2, h3, dup_hash],
)

# Tree is now corrupted: root hash includes ghost leaf for dup_key
# check_integrity() will raise because leaf_count != cache length
try:
    blob.check_integrity()
    print("BUG: integrity check should have failed")
except Exception as e:
    print(f"Integrity failure confirmed: {e}")
``` [8](#0-7)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L2254-2295)
```rust
    #[rstest]
    fn test_batch_insert(
        #[values(0, 1, 2, 10)] pre_inserts: usize,
        #[values(0, 1, 2, 8, 9)] count: usize,
    ) {
        let mut blob = MerkleBlob::new(vec![]).unwrap();
        for i in 0..pre_inserts {
            let i = i as i64;
            blob.insert(
                KeyId(i),
                ValueId(i),
                &sha256_num(&i),
                InsertLocation::Auto {},
            )
            .unwrap();
        }
        open_dot(blob.to_dot().unwrap().set_note("initial"));

        let mut batch: Vec<((KeyId, ValueId), Hash)> = vec![];

        let mut batch_map: HashMap<KeyId, ValueId> = HashMap::new();
        for i in pre_inserts..(pre_inserts + count) {
            let i = i as i64;
            batch.push(((KeyId(i), ValueId(i)), sha256_num(&i)));
            batch_map.insert(KeyId(i), ValueId(i));
        }

        let before = blob.get_keys_values().unwrap();
        blob.batch_insert(batch).unwrap();
        let after = blob.get_keys_values().unwrap();

        open_dot(
            blob.to_dot()
                .unwrap()
                .set_note(&format!("after batch insert of {count} values")),
        );

        let mut expected = before.clone();
        expected.extend(batch_map);

        assert_eq!(after, expected);
    }
```
