### Title
`BlockStatusCache::move_index` Fails to Update Leaf Index Maps on Root-Collapse Delete, Corrupting DataLayer Merkle State - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`BlockStatusCache::move_index` only updates `free_indexes` when a node is relocated during deletion. When the deleted leaf's parent is the tree root (exactly 2-leaf tree collapsing to 1 leaf), the sibling leaf is physically moved to `TreeIndex(0)`, but `key_to_index` and `leaf_hash_to_index` are never updated to reflect the new position. This leaves both maps pointing to a now-free stale index, causing subsequent `get_proof_of_inclusion`, `get_keys_values`, and `get_node_by_hash` calls to read from the wrong or reused slot — corrupting DataLayer Merkle state and proof generation.

### Finding Description

In `MerkleBlob::delete`, when the parent of the deleted leaf has no grandparent (i.e., the parent is the root), the sibling node is moved to `TreeIndex(0)`:

```rust
// blob.rs lines 752-765
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
        .move_index(sibling_index, destination)?;   // <-- BUG
    return Ok(());
};
```

The `if let Node::Internal` guard correctly updates children's parent pointers for internal siblings, but for **leaf siblings** it does nothing extra. Then `move_index` is called:

```rust
// blob.rs lines 210-224
fn move_index(&mut self, source: TreeIndex, destination: TreeIndex) -> Result<(), Error> {
    if self.free_indexes.contains(&source) {
        return Err(Error::MoveSourceIndexNotInUse(source));
    }
    if self.free_indexes.contains(&destination) {
        return Err(Error::MoveDestinationIndexNotInUse(destination));
    }
    self.free_indexes.insert(source);   // marks old slot free
    Ok(())                              // key_to_index and leaf_hash_to_index NOT updated
}
```

`move_index` only marks `source` as free. It never updates `key_to_index` or `leaf_hash_to_index`. For an internal sibling this is harmless (those maps only track leaves). For a **leaf sibling**, both maps retain the stale `source` index instead of being updated to `destination`.

**Concrete 2-leaf scenario:**

| Slot | Before delete(A) | After delete(A) |
|------|-----------------|-----------------|
| 0 | Internal root (left=1, right=2) | Leaf B (moved here) |
| 1 | Leaf A | stale (free) |
| 2 | Leaf B | stale (free, but `key_to_index[B.key]` still = 2) |

After the delete:
- `key_to_index = { B.key → 2 }` ← **stale**, should be `0`
- `leaf_hash_to_index = { B.hash → 2 }` ← **stale**, should be `0`
- `free_indexes = { 1, 2 }`

**Consequence 1 — immediate panic in `get_proof_of_inclusion(B.key)`:**

```rust
// blob.rs lines 1159-1179
let mut index = *self.block_status_cache.get_index_by_key(key)...;
// index = 2 (stale)
let parents = self.get_lineage_blocks_with_indexes(index)?;
// lineage: [(2, stale_leaf_B{parent=0}), (0, leaf_B_new)]
parents_iter.next(); // skip self
for (next_index, block) in parents_iter {
    let parent = block.node.expect_internal("all nodes after the first should be internal");
    // index 0 is now a Leaf → PANIC
```

`expect_internal` unconditionally panics when called on a `Node::Leaf`:

```rust
// format.rs lines 285-292
pub fn expect_internal(&self, message: &str) -> InternalNode {
    let Node::Internal(internal) = self else {
        panic!("{}", message)   // panics here
    };
    *internal
}
```

**Consequence 2 — silent data corruption after a subsequent insert:**

After the delete, `free_indexes = {1, 2}`. A subsequent `insert` calls `pop_free_index()` and may allocate index 2 for a new leaf C. `add_leaf(2, C)` sets `key_to_index[C.key] = 2`. Now:

- `key_to_index = { B.key → 2, C.key → 2 }` — two distinct keys map to the same slot
- `get_keys_values()` iterates both entries, reads slot 2 (C's data) for both, and returns `{ B.key → C.value, C.key → C.value }` — B's value is silently replaced by C's value
- `get_proof_of_inclusion(B.key)` generates a proof whose `node_hash` is C's hash, not B's hash — the proof is structurally invalid for B

The `check_integrity` function would detect this (line 845: `if *cached_index != index { return Err(...) }`), but it is not called automatically after every mutation in production paths.

### Impact Explanation

This is a **High** severity DataLayer Merkle state corruption bug. After any `delete` that reduces a 2-leaf tree to 1 leaf:

1. `get_proof_of_inclusion` for the surviving key **panics**, crashing the DataLayer node process.
2. After a subsequent insert, `get_keys_values` silently returns wrong values — the surviving key B is mapped to the newly inserted key C's value.
3. Proof generation for key B produces a proof carrying C's hash, making all inclusion proofs for B structurally invalid.

The DataLayer's key-value mapping and proof state are corrupted without any error being surfaced to the caller.

### Likelihood Explanation

The trigger condition — deleting one of exactly two leaves — is a routine, low-privilege DataLayer operation. Any DataLayer store that starts with two entries and has one deleted hits this path. No special permissions, keys, or network access are required beyond the ability to perform a standard `delete` call on the `MerkleBlob`. The bug is deterministic and reproducible on every such deletion.

### Recommendation

`move_index` must also remap `key_to_index` and `leaf_hash_to_index` when the moved node is a leaf. The cleanest fix is to pass the sibling node to the cache update so it can distinguish leaf from internal:

```rust
// In delete(), replace:
self.block_status_cache.move_index(sibling_index, destination)?;

// With a leaf-aware variant, e.g.:
match sibling_block.node {
    Node::Leaf(ref leaf) => {
        self.block_status_cache.move_leaf_index(leaf, sibling_index, destination)?;
    }
    Node::Internal(_) => {
        self.block_status_cache.move_index(sibling_index, destination)?;
    }
}
```

Where `move_leaf_index` additionally does:
```rust
self.key_to_index.insert(leaf.key, destination);
self.leaf_hash_to_index.insert(leaf.hash, destination);
```

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};

fn sha256_val(n: i64) -> Hash { /* ... */ }

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Insert two leaves → 2-leaf tree
blob.insert(KeyId(1), ValueId(10), &sha256_val(1), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(20), &sha256_val(2), InsertLocation::Auto {}).unwrap();

// Delete one leaf → triggers the root-collapse path
blob.delete(KeyId(1)).unwrap();

// key_to_index[2] is now stale (points to old index, not TreeIndex(0))
// This panics: "all nodes after the first should be internal"
let _proof = blob.get_proof_of_inclusion(KeyId(2)).unwrap();
```

The panic occurs at `blob.rs:1179` (`expect_internal`) because the stale index 2's parent pointer leads back to `TreeIndex(0)`, which is now a `Node::Leaf`, not a `Node::Internal`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1135-1146)
```rust
    pub fn get_keys_values(&self) -> Result<HashMap<KeyId, ValueId>, Error> {
        let mut map = HashMap::new();
        for (key, index) in self.block_status_cache.iter_keys_indexes() {
            let node = self.get_node(*index)?;
            let leaf = node.expect_leaf(
                "key was just retrieved from the key to index mapping, must be a leaf",
            );
            map.insert(*key, leaf.value);
        }

        Ok(map)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1179)
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
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L285-292)
```rust
    pub fn expect_internal(&self, message: &str) -> InternalNode {
        let Node::Internal(internal) = self else {
            let message = message.replace("<<self>>", &format!("{self:?}"));
            panic!("{}", message)
        };

        *internal
    }
```
