### Title
Stale `key_to_index` / `leaf_hash_to_index` Cache After Leaf Move in `BlockStatusCache::move_index` — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`BlockStatusCache::move_index` updates only `free_indexes` when a leaf node is physically relocated in the blob, but never updates `key_to_index` or `leaf_hash_to_index`. This leaves stale cache entries pointing to the now-free source slot. After a subsequent insert reuses that slot, every cache-driven lookup for the moved leaf silently resolves to the wrong node, corrupting all further Merkle operations on that key.

---

### Finding Description

`BlockStatusCache` maintains three parallel structures:

| Field | Purpose |
|---|---|
| `free_indexes` | Tracks which blob slots are available |
| `key_to_index` | Maps `KeyId → TreeIndex` for O(1) leaf lookup |
| `leaf_hash_to_index` | Maps `Hash → TreeIndex` for O(1) hash lookup |

`move_index` is the only function that relocates a node from one slot to another. Its implementation is:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 210-224
fn move_index(&mut self, source: TreeIndex, destination: TreeIndex) -> Result<(), Error> {
    // to be called _after_ having written to the destination index
    if self.free_indexes.contains(&source) {
        return Err(Error::MoveSourceIndexNotInUse(source));
    }
    if self.free_indexes.contains(&destination) {
        return Err(Error::MoveDestinationIndexNotInUse(destination));
    }

    self.free_indexes.insert(source);   // ← only free_indexes is updated

    Ok(())
}
``` [1](#0-0) 

`key_to_index` and `leaf_hash_to_index` are **never updated** to reflect that the leaf now lives at `destination` instead of `source`.

`move_index` is called in exactly one place — the no-grandparent branch of `delete()`, where the sibling of the deleted leaf is promoted to become the new root at `TreeIndex(0)`:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 752-765
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
        .move_index(sibling_index, destination)?;   // ← stale cache left here

    return Ok(());
};
``` [2](#0-1) 

When the sibling is a **leaf** (the `if let Node::Internal` branch is skipped), after `move_index` returns:

- `free_indexes` correctly contains `sibling_index` (old slot is free) ✓  
- `key_to_index[sibling.key]` still equals `sibling_index` ✗ (should be `TreeIndex(0)`)  
- `leaf_hash_to_index[sibling.hash]` still equals `sibling_index` ✗ (should be `TreeIndex(0)`)

The old slot `sibling_index` is now free and eligible for reuse by the next `insert`. Once a new leaf C is inserted there, `key_to_index` contains two entries pointing to the same slot:

```
sibling.key  →  sibling_index   (stale — now holds C's data)
C.key        →  sibling_index   (fresh)
```

Every subsequent cache-driven operation on `sibling.key` silently operates on C's node instead.

---

### Impact Explanation

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

Concrete consequences after the stale entry is activated by a reuse insert:

1. **`get_proof_of_inclusion(sibling.key)`** — reads C's node from the blob, constructs a proof for C's hash under sibling's key. The proof fails `valid()` (hash mismatch), so the key appears unprovable even though it exists in the tree.

2. **`upsert(sibling.key, new_value, new_hash)`** — calls `remove_leaf` on C's node data (removing C's hash from the cache), then overwrites C's blob slot with the new hash. The actual sibling node at `TreeIndex(0)` is never touched. The tree root is recomputed from C's modified slot, producing a root that does not correspond to the logical key-value state.

3. **`delete(sibling.key)`** — resolves to C's slot, deletes C's node from the blob and cache, and leaves the real sibling node at `TreeIndex(0)` as an orphaned, unreachable root. The tree is permanently corrupted.

All three outcomes corrupt the Merkle root stored in the DataLayer, invalidating all future inclusion/exclusion proofs derived from it.

---

### Likelihood Explanation

The trigger sequence is entirely routine DataLayer usage:

1. Insert two leaves (A and B) — tree has 2 leaves, root at index 0, leaves at indices 1 and 2.  
2. Delete leaf A — sibling B is moved to index 0; `sibling_index` (e.g., 2) is freed; cache still maps `B.key → 2`.  
3. Insert any new leaf C — slot 2 is reused; cache now has `B.key → 2` (stale) and `C.key → 2`.  
4. Any operation on `B.key` silently operates on C's data.

No privileged access, no adversarial input, and no unusual configuration is required. Any DataLayer store that grows from 2 entries and then shrinks back to 1 before growing again will hit this path.

---

### Recommendation

`move_index` must update all three cache structures. When the moved node is a leaf, `key_to_index` and `leaf_hash_to_index` must be redirected from `source` to `destination`. The caller already has the `sibling_block` in scope, so the leaf data is available:

```rust
fn move_index(&mut self, source: TreeIndex, destination: TreeIndex, node: &Node) -> Result<(), Error> {
    if self.free_indexes.contains(&source) {
        return Err(Error::MoveSourceIndexNotInUse(source));
    }
    if self.free_indexes.contains(&destination) {
        return Err(Error::MoveDestinationIndexNotInUse(destination));
    }

    self.free_indexes.insert(source);

    // Keep key/hash maps consistent with the new physical location.
    if let Node::Leaf(leaf) = node {
        if let Some(idx) = self.key_to_index.get_mut(&leaf.key) {
            *idx = destination;
        }
        if let Some(idx) = self.leaf_hash_to_index.get_mut(&leaf.hash) {
            *idx = destination;
        }
    }

    Ok(())
}
```

The call site in `delete()` already holds `sibling_block` and can pass `&sibling_block.node`.

---

### Proof of Concept

```rust
// Reproduces stale-cache corruption in a 2-leaf → 1-leaf → 2-leaf cycle.
use chia_datalayer::{KeyId, ValueId, MerkleBlob, InsertLocation};

let hash_a = [1u8; 32];
let hash_b = [2u8; 32];
let hash_c = [3u8; 32];

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Step 1: insert A and B (2-leaf tree, root=0, leaves at 1 and 2)
blob.insert(KeyId(1), ValueId(1), &hash_a, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(2), &hash_b, InsertLocation::Auto {}).unwrap();

// Step 2: delete A — B is moved to TreeIndex(0); old slot (e.g. 2) freed;
//         cache still maps KeyId(2) → old slot.
blob.delete(KeyId(1)).unwrap();

// Step 3: insert C — reuses the freed slot; cache now has
//         KeyId(2) → old_slot (stale) AND KeyId(3) → old_slot (fresh).
blob.insert(KeyId(3), ValueId(3), &hash_c, InsertLocation::Auto {}).unwrap();

// Step 4: proof for B resolves to C's node — hash mismatch, proof invalid.
blob.calculate_lazy_hashes().unwrap();
let proof = blob.get_proof_of_inclusion(KeyId(2)).unwrap();
assert!(proof.valid(), "proof for B is invalid — tree root corrupted");
// ^^^ this assertion FAILS, demonstrating the corruption.
``` [3](#0-2) [4](#0-3)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L199-224)
```rust
    fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
        let Some(index) = self.key_to_index.remove(&node.key) else {
            return Err(Error::UnknownKey(node.key));
        };
        self.leaf_hash_to_index.remove(&node.hash);

        self.free_indexes.insert(index);

        Ok(())
    }

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L736-765)
```rust
    pub fn delete(&mut self, key: KeyId) -> Result<(), Error> {
        let (leaf_index, leaf, _leaf_block) = self.get_leaf_by_key(key)?;
        self.block_status_cache.remove_leaf(&leaf)?;

        let Some(parent_index) = leaf.parent.0 else {
            self.clear();
            return Ok(());
        };

        let maybe_parent = self.get_node(parent_index)?;
        let Node::Internal(parent) = maybe_parent else {
            panic!("parent node not internal: {maybe_parent:?}")
        };
        let sibling_index = parent.sibling_index(leaf_index)?;
        let mut sibling_block = self.get_block(sibling_index)?;

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
