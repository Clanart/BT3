### Title
`MerkleBlob::upsert` Removes Leaf from `BlockStatusCache` Without Re-Inserting It, Marking the Live Index as Free — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::upsert` calls `block_status_cache.remove_leaf()` to evict the old leaf entry from the in-memory cache, then writes the updated leaf back to the blob, but **never calls `block_status_cache.add_leaf()`** to re-register the updated leaf. After the call returns, the cache believes the leaf's storage index is free, the key is absent from `key_to_index`, and the new hash is absent from `leaf_hash_to_index`. Any subsequent insert will reclaim that "free" index and silently overwrite the live leaf, corrupting the Merkle tree and invalidating all proofs derived from it.

---

### Finding Description

`MerkleBlob::upsert` (lines 792–810) handles the case where a key already exists:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 792-810
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    self.block_status_cache.remove_leaf(&leaf)?;   // ← evicts key, old hash, marks index FREE
    leaf.hash.clone_from(new_hash);
    leaf.value = value;
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?; // ← writes bytes only, no cache update

    if let Some(parent) = block.node.parent().0 {
        self.mark_lineage_as_dirty(parent)?;
    }
    Ok(())
    // ← add_leaf() is NEVER called
}
```

`BlockStatusCache::remove_leaf` (lines 199–208) does three things:

```rust
fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
    let Some(index) = self.key_to_index.remove(&node.key) else { … };
    self.leaf_hash_to_index.remove(&node.hash);
    self.free_indexes.insert(index);   // ← index now considered available
    Ok(())
}
```

`BlockStatusCache::add_leaf` (lines 188–193) is the symmetric counterpart that must be called after writing the updated leaf:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);   // ← reclaim from free list
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
```

`upsert` calls `remove_leaf` but never calls `add_leaf`. After `upsert` returns:

| Cache field | Expected state | Actual state |
|---|---|---|
| `key_to_index[key]` | points to `leaf_index` | **absent** |
| `leaf_hash_to_index[new_hash]` | points to `leaf_index` | **absent** |
| `free_indexes` | does **not** contain `leaf_index` | **contains** `leaf_index` |

The blob bytes at `leaf_index` are correct, but the cache is completely inconsistent with them.

---

### Impact Explanation

**Immediate corruption path:** `get_new_index()` calls `pop_free_index()`, which returns the first element of `free_indexes`. Because `leaf_index` was inserted there by `remove_leaf`, the very next `insert` call receives `leaf_index` as the allocation target and overwrites the live leaf with a brand-new leaf. The old key/value/hash is silently destroyed. The parent chain is marked dirty and the root hash is recomputed over the corrupted tree.

**Proof invalidity:** After the overwrite, any previously generated inclusion proof for the upserted key is invalid (the leaf no longer exists at that position). Any exclusion proof for the newly inserted key is also invalid (the tree root has changed). A verifier using the old root accepts forged state; a verifier using the new root rejects valid state.

**`check_integrity` detection:** `check_integrity` (lines 812–819) compares `leaf_count` (from iterating the blob) with `key_to_index_cache_length`. After `upsert`, `leaf_count` is unchanged but `key_to_index_cache_length` is one less, so integrity fails — but only if the caller explicitly invokes `check_integrity`. Normal DataLayer operation does not gate every mutation on an integrity check.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`upsert` is a first-class public API of `MerkleBlob`. Any DataLayer operation that updates an existing key (e.g., a delta that modifies a value) followed by any insert (e.g., adding a new key in the same batch) triggers the corruption. No special privileges are required; the attacker only needs to submit two sequential DataLayer operations: one update and one insert. The bug is deterministic and reproducible.

---

### Recommendation

After writing the updated leaf to the blob, call `add_leaf` with the **new** leaf data:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    leaf.value = value;
    block.node = Node::Leaf(leaf.clone());
    self.insert_entry_to_blob(leaf_index, &block)?;
+   self.block_status_cache.add_leaf(leaf_index, leaf); // re-register updated leaf

    if let Some(parent) = block.node.parent().0 {
        self.mark_lineage_as_dirty(parent)?;
    }
    Ok(())
}
```

Alternatively, avoid the remove/add round-trip entirely by updating `key_to_index` and `leaf_hash_to_index` in place (remove old hash entry, insert new hash entry, leave `key_to_index` and `free_indexes` untouched).

---

### Proof of Concept

```rust
use chia_datalayer::MerkleBlob;

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Insert key=1 with hash_a
blob.insert(KeyId(1), ValueId(1), &hash_a, InsertLocation::Auto {}).unwrap();

// Upsert key=1 with hash_b  →  after this, leaf_index is in free_indexes
blob.upsert(KeyId(1), ValueId(2), &hash_b).unwrap();

// Insert key=2  →  get_new_index() returns the "free" leaf_index,
// overwriting the upserted key=1 leaf
blob.insert(KeyId(2), ValueId(2), &hash_c, InsertLocation::Auto {}).unwrap();

// check_integrity now fails: leaf_count != key_to_index_cache_length
// and the root hash is computed over a tree that lost key=1
blob.check_integrity().unwrap_err(); // IntegrityKeyToIndexCacheLength
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L136-143)
```rust
    fn pop_free_index(&mut self) -> Option<TreeIndex> {
        let maybe_index = self.free_indexes.iter().next().copied();
        if let Some(index) = maybe_index {
            self.free_indexes.shift_remove(&index);
        }

        maybe_index
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-208)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }

    fn remove_internal(&mut self, index: TreeIndex) {
        self.free_indexes.insert(index);
    }

    fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
        let Some(index) = self.key_to_index.remove(&node.key) else {
            return Err(Error::UnknownKey(node.key));
        };
        self.leaf_hash_to_index.remove(&node.hash);

        self.free_indexes.insert(index);

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L792-810)
```rust
    pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
        let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
            self.insert(key, value, new_hash, InsertLocation::Auto {})?;
            return Ok(());
        };

        self.block_status_cache.remove_leaf(&leaf)?;
        leaf.hash.clone_from(new_hash);
        leaf.value = value;
        // OPT: maybe just edit in place?
        block.node = Node::Leaf(leaf);
        self.insert_entry_to_blob(leaf_index, &block)?;

        if let Some(parent) = block.node.parent().0 {
            self.mark_lineage_as_dirty(parent)?;
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-879)
```rust
        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
        let total_count = leaf_count + internal_count + self.block_status_cache.free_index_count();
        let extend_index = self.extend_index();
        if total_count != extend_index.0 as usize {
            return Err(Error::IntegrityTotalNodeCount(extend_index, total_count));
        }
```
