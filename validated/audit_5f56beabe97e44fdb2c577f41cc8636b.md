### Title
`BlockStatusCache::move_index` Fails to Update Leaf Cache Mappings After Node Relocation, Causing Stale Index State and Forged Proofs of Inclusion - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`BlockStatusCache::move_index` only marks the source slot as free in `free_indexes` but never updates `key_to_index` or `leaf_hash_to_index` to reflect the new position of the moved leaf. This is the direct analog of the Velocimeter swap-and-pop index-tracking bug: a tracked collection element is relocated, but the reverse mapping is not updated. The only call site is `MerkleBlob::delete` when the deleted leaf's parent is the root (i.e., the tree shrinks from 2 leaves to 1). After that deletion the surviving leaf's cache entries permanently point to a now-free slot. Any subsequent insert that reuses that slot causes `get_proof_of_inclusion`, `get_keys_values`, and `get_key_index` to silently operate on the wrong node, producing forged or invalid DataLayer Merkle proofs.

---

### Finding Description

**Root cause — `move_index` does not update leaf cache maps**

`BlockStatusCache` maintains three parallel structures:

| Field | Purpose |
|---|---|
| `free_indexes` | set of unoccupied blob slots |
| `key_to_index` | `KeyId → TreeIndex` for every leaf |
| `leaf_hash_to_index` | `Hash → TreeIndex` for every leaf | [1](#0-0) 

`move_index` is documented as the function to call after physically writing a node to a new blob slot. It correctly marks the old slot as free, but it never touches `key_to_index` or `leaf_hash_to_index`: [2](#0-1) 

Compare with `add_leaf`, which correctly inserts into all three structures: [3](#0-2) 

**Trigger path — `delete` when parent is the root**

`MerkleBlob::delete` has a special branch for the case where the deleted leaf's parent has no grandparent (i.e., the tree has exactly 2 leaves). The sibling leaf is physically written to `TreeIndex(0)` and `move_index` is called: [4](#0-3) 

After this returns:
- The blob at `TreeIndex(0)` correctly holds the surviving leaf.
- `free_indexes` correctly contains `sibling_index` (old slot).
- **`key_to_index[sibling.key]` still maps to `sibling_index` (stale — should be `TreeIndex(0)`).**
- **`leaf_hash_to_index[sibling.hash]` still maps to `sibling_index` (stale — should be `TreeIndex(0)`).**

**Exploitation sequence**

1. Insert two leaves (key A at slot 1, key B at slot 2; root internal node at slot 0).
2. Delete key A. The sibling (key B, slot 2) is moved to slot 0. `move_index(2, 0)` adds slot 2 to `free_indexes` but leaves `key_to_index[B] = 2`.
3. Insert a new leaf (key C). The allocator picks slot 2 (it is free) and writes key C there. `add_leaf` sets `key_to_index[C] = 2`.
4. Now `key_to_index[B] = 2` and `key_to_index[C] = 2` — two keys map to the same slot.
5. `get_proof_of_inclusion(B)` reads slot 2, finds key C's node, and builds a proof for key C's hash under key B's identity.
6. `get_keys_values()` returns key C's value for key B. [5](#0-4) 

`check_integrity` would detect the mismatch (cached index ≠ actual index), but it is only called explicitly or on drop, not on every read: [6](#0-5) 

The Python and WASM bindings expose `get_proof_of_inclusion` and `get_keys_values` directly to untrusted callers: [7](#0-6) 

---

### Impact Explanation

This is a **High** severity DataLayer finding. After the two-leaf delete, the `BlockStatusCache` is silently inconsistent. Any subsequent insert causes `get_proof_of_inclusion` to return a proof whose `node_hash` belongs to a different key than the one queried. A verifier comparing that proof's `root_hash()` against the committed tree root will accept it as valid for the wrong key, constituting a forged proof of inclusion. `get_keys_values` will also return wrong values, corrupting any application logic that relies on the DataLayer key-value store.

---

### Likelihood Explanation

The trigger condition — deleting one of exactly two leaves — is a routine DataLayer operation. Any DataLayer store that starts small (2 entries) and then grows will hit this path. The bug is silent (no error is returned, no panic occurs) and persists until `check_integrity` is explicitly called. The Python/WASM bindings make this reachable from unprivileged input with no special privileges required.

---

### Recommendation

`move_index` must update `key_to_index` and `leaf_hash_to_index` for the moved node. Since `move_index` does not currently know the node type, the simplest fix is to perform the cache update in the `delete` call site, immediately after `move_index`, conditioned on the sibling being a leaf:

```rust
// in the no-grandparent branch of delete(), after move_index():
if let Node::Leaf(ref leaf) = sibling_block.node {
    // update stale cache entries to point to the new position
    self.block_status_cache.key_to_index.insert(leaf.key, destination);
    self.block_status_cache.leaf_hash_to_index.insert(leaf.hash, destination);
}
```

Alternatively, give `move_index` access to the moved node so it can update all three maps atomically, mirroring the symmetry already present in `add_leaf` / `remove_leaf`.

---

### Proof of Concept

```rust
#[test]
fn test_move_index_stale_cache_after_two_leaf_delete() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    blob.check_integrity_on_drop = false;

    let hash_a: Hash = [0xAA; 32];
    let hash_b: Hash = [0xBB; 32];
    let hash_c: Hash = [0xCC; 32];

    let key_a = KeyId(1);
    let key_b = KeyId(2);
    let key_c = KeyId(3);

    // Build a 2-leaf tree
    blob.insert(key_a, ValueId(10), &hash_a, InsertLocation::Auto {}).unwrap();
    blob.insert(key_b, ValueId(20), &hash_b, InsertLocation::Auto {}).unwrap();

    // Delete key_a: sibling (key_b) is moved to TreeIndex(0)
    // move_index() marks old slot as free but does NOT update key_to_index[key_b]
    blob.delete(key_a).unwrap();

    // The old slot for key_b is now in free_indexes
    // Insert key_c: allocator reuses that free slot
    blob.insert(key_c, ValueId(30), &hash_c, InsertLocation::Auto {}).unwrap();

    // key_to_index[key_b] and key_to_index[key_c] now both point to the same slot
    let idx_b = blob.get_key_index(key_b).unwrap();
    let idx_c = blob.get_key_index(key_c).unwrap();
    assert_ne!(idx_b, idx_c, "BUG: two keys share the same cached index");

    // check_integrity reveals the corruption
    blob.check_integrity().expect_err("integrity should fail due to stale cache");
}
``` [2](#0-1) [4](#0-3)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1162)
```rust
    pub fn get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
        let mut index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1542-1548)
```rust
    #[pyo3(name = "get_proof_of_inclusion")]
    pub fn py_get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> PyResult<proof_of_inclusion::ProofOfInclusion> {
        Ok(self.get_proof_of_inclusion(key)?)
    }
```
