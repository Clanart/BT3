### Title
Stale `key_to_index` / `leaf_hash_to_index` Cache After Leaf-Sibling Move in `delete` — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`BlockStatusCache::move_index` fails to update the `key_to_index` and `leaf_hash_to_index` maps when a leaf node is physically relocated to a new blob index. This is triggered by `MerkleBlob::delete` whenever the deleted leaf's parent is the tree root and the surviving sibling is itself a leaf. After the operation the two cache maps permanently point to the now-freed source index, making every subsequent lookup for that leaf return stale or wrong data and allowing a later insert to silently overwrite the freed slot so that the surviving leaf's key resolves to a completely different node.

---

### Finding Description

`BlockStatusCache` maintains three parallel data structures:

| field | purpose |
|---|---|
| `free_indexes` | set of blob slots available for reuse |
| `key_to_index` | `KeyId → TreeIndex` for every live leaf |
| `leaf_hash_to_index` | `Hash → TreeIndex` for every live leaf | [1](#0-0) 

When a node is relocated, `move_index` is called to keep the cache consistent:

```rust
fn move_index(&mut self, source: TreeIndex, destination: TreeIndex) -> Result<(), Error> {
    if self.free_indexes.contains(&source) {
        return Err(Error::MoveSourceIndexNotInUse(source));
    }
    if self.free_indexes.contains(&destination) {
        return Err(Error::MoveDestinationIndexNotInUse(destination));
    }
    self.free_indexes.insert(source);   // ← only free_indexes is updated
    Ok(())
}
``` [2](#0-1) 

`key_to_index` and `leaf_hash_to_index` are **never updated** by this function.

The only call site is inside `delete`, in the branch that fires when the deleted leaf's parent is the root (no grandparent). The sibling node is physically written to `TreeIndex(0)` and then `move_index(sibling_index, TreeIndex(0))` is called:

```rust
let Some(grandparent_index) = parent.parent.0 else {
    sibling_block.node.set_parent(Parent(None));
    let destination = TreeIndex(0);
    if let Node::Internal(node) = sibling_block.node {
        for child_index in [node.left, node.right] {
            self.update_parent(child_index, Some(destination))?;
        }
    }
    self.insert_entry_to_blob(destination, &sibling_block)?;
    self.block_status_cache
        .move_index(sibling_index, destination)?;   // ← cache not fully updated
    return Ok(());
};
``` [3](#0-2) 

When `sibling_block.node` is a `Node::Leaf` (the `if let Node::Internal` branch is skipped), the leaf's data is written to `TreeIndex(0)` but:

- `key_to_index[sibling.key]` still holds `sibling_index` (now freed)
- `leaf_hash_to_index[sibling.hash]` still holds `sibling_index` (now freed)

The `check_integrity` validator confirms this is a real inconsistency — it explicitly checks that every leaf's key maps to the correct index and returns `IntegrityKeyToIndexCacheIndex` when they disagree: [4](#0-3) 

---

### Impact Explanation

After the stale-cache state is established:

1. **Immediate wrong reads.** `get_leaf_by_key(sibling.key)` resolves to the freed `sibling_index` and reads whatever bytes happen to be there. [5](#0-4) 

2. **Wrong proof of inclusion.** `get_proof_of_inclusion(sibling.key)` uses the stale index to build the Merkle path, producing a proof that does not correspond to the surviving leaf's actual position in the tree. [6](#0-5) 

3. **Silent key hijack after reuse.** `sibling_index` is now in `free_indexes`. The next `insert` call may allocate it for a completely different leaf C. After that, `key_to_index[sibling.key]` → `sibling_index` → leaf C's data. Every lookup for the surviving leaf B now silently returns leaf C's key/value pair, and `get_node_by_hash(sibling.hash)` returns leaf C's `(KeyId, ValueId)`. [7](#0-6) 

This directly enables **forged inclusion proofs**: a proof generated for key B after the slot is reused will be structurally valid (it passes `ProofOfInclusion::verify`) but will attest to the wrong leaf data, corrupting DataLayer state visible to clients and other nodes.

---

### Likelihood Explanation

The trigger condition — a two-leaf tree followed by deletion of one leaf — is a routine DataLayer operation. Any unprivileged actor who can submit DataLayer store updates (inserts and deletes) can reach this path. No special privileges, keys, or network position are required. The bug is deterministic and reproducible with a fixed sequence of three operations: `insert(A)`, `insert(B)`, `delete(A)`.

---

### Recommendation

`move_index` must update all three cache maps, not just `free_indexes`. When the node being moved is a leaf, both `key_to_index` and `leaf_hash_to_index` must be updated to point to `destination`. The function should read the node type from the blob (already written to `destination` before `move_index` is called, per the comment "to be called _after_ having written to the destination index") and perform the appropriate map updates:

```rust
fn move_index(&mut self, source: TreeIndex, destination: TreeIndex, node: &Node) -> Result<(), Error> {
    if self.free_indexes.contains(&source) {
        return Err(Error::MoveSourceIndexNotInUse(source));
    }
    if self.free_indexes.contains(&destination) {
        return Err(Error::MoveDestinationIndexNotInUse(destination));
    }
    self.free_indexes.insert(source);
    if let Node::Leaf(leaf) = node {
        self.key_to_index.insert(leaf.key, destination);
        self.leaf_hash_to_index.insert(leaf.hash, destination);
    }
    Ok(())
}
``` [2](#0-1) 

---

### Proof of Concept

```
// 1. Build a two-leaf tree
let mut blob = MerkleBlob::new(vec![]).unwrap();
blob.insert(KeyId(1), ValueId(10), &hash_a, InsertLocation::Auto{}).unwrap();
blob.insert(KeyId(2), ValueId(20), &hash_b, InsertLocation::Auto{}).unwrap();
// Tree: [0]=Internal, [1]=Leaf(key=1), [2]=Leaf(key=2)

// 2. Delete key=1 → triggers the root-parent branch
blob.delete(KeyId(1)).unwrap();
// Leaf(key=2) is physically moved to TreeIndex(0)
// BUT key_to_index[KeyId(2)] still == TreeIndex(2) (freed!)

// 3. Insert a new leaf — reuses freed TreeIndex(2)
blob.insert(KeyId(3), ValueId(30), &hash_c, InsertLocation::Auto{}).unwrap();
// TreeIndex(2) now holds Leaf(key=3)

// 4. Lookup key=2 — silently returns key=3's data
let (k, v) = blob.get_node_by_hash(hash_b).unwrap();
// k == KeyId(3), v == ValueId(30)  ← wrong!

// 5. Proof of inclusion for key=2 is built from wrong index
let proof = blob.get_proof_of_inclusion(KeyId(2)).unwrap();
// proof attests to Leaf(key=3), not Leaf(key=2)

// 6. check_integrity confirms the corruption
assert!(blob.check_integrity().is_err()); // IntegrityKeyToIndexCacheIndex
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L88-94)
```rust
#[cfg_attr(feature = "py-bindings", pyclass(from_py_object))]
#[derive(Clone, Debug)]
pub struct BlockStatusCache {
    free_indexes: IndexSet<TreeIndex>,
    key_to_index: HashMap<KeyId, TreeIndex>,
    leaf_hash_to_index: HashMap<Hash, TreeIndex>,
}
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L210-224)
```rust
    fn move_index(&mut self, source: TreeIndex, destination: TreeIndex) -> Result<(), Error> {
        // to be called _after_ having written to the destination index
        // TODO: not checking it is within bounds of the present blob
        if self.free_indexes.contains(&source) {
            return Err(Error::MoveSourceIndexNotInUse(source));
        }
        // TODO: not checking it is within bounds of the present blob
        if self.free_indexes.contains(&destination) {
            return Err(Error::MoveDestinationIndexNotInUse(destination));
        }

        self.free_indexes.insert(source);

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L752-765)
```rust
        let Some(grandparent_index) = parent.parent.0 else {
            sibling_block.node.set_parent(Parent(None));
            let destination = TreeIndex(0);
            if let Node::Internal(node) = sibling_block.node {
                for child_index in [node.left, node.right] {
                    self.update_parent(child_index, Some(destination))?;
                }
            }

            self.insert_entry_to_blob(destination, &sibling_block)?;
            self.block_status_cache
                .move_index(sibling_index, destination)?;

            return Ok(());
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L841-851)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1053-1063)
```rust
    pub fn get_leaf_by_key(&self, key: KeyId) -> Result<(TreeIndex, LeafNode, Block), Error> {
        let index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;
        let block = self.get_block(index)?;
        let leaf = block.node.expect_leaf(&format!(
            "expected leaf for index from key cache: {index} -> <<self>>"
        ));

        Ok((index, leaf, block))
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1198-1208)
```rust
    pub fn get_node_by_hash(&self, node_hash: Hash) -> Result<(KeyId, ValueId), Error> {
        let Some(index) = self.block_status_cache.get_index_by_leaf_hash(&node_hash) else {
            return Err(Error::LeafHashNotFound(node_hash));
        };

        let node = self
            .get_node(*index)?
            .expect_leaf("should only have leaves in the leaf hash to index cache");

        Ok((node.key, node.value))
    }
```
