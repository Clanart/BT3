### Title
Missing Hash-Conflict Guard in `MerkleBlob::upsert` Silently Corrupts `leaf_hash_to_index` Cache — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::upsert` does not check whether `new_hash` is already present in the tree before mutating state. When `new_hash` equals the hash of a *different* existing leaf, `remove_leaf` removes the old hash from the cache, then `add_leaf` silently overwrites the surviving entry for `new_hash` in `leaf_hash_to_index`. The result is a blob containing two leaves with the same hash while the cache maps that hash to only one of them — a committed, persistent DataLayer state corruption.

---

### Finding Description

`MerkleBlob::insert` guards against duplicate hashes at lines 372-374:

```rust
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`MerkleBlob::upsert` has no equivalent guard:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    self.block_status_cache.remove_leaf(&leaf)?;   // ← removes old hash
    leaf.hash.clone_from(new_hash);
    leaf.value = value;
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?; // ← add_leaf overwrites H2 entry
    ...
}
``` [2](#0-1) 

`insert_entry_to_blob` unconditionally calls `add_leaf`, which calls `HashMap::insert` — silently overwriting any existing `leaf_hash_to_index` entry for the same hash:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index); // ← no conflict check
}
``` [3](#0-2) 

**Step-by-step corruption trace:**

| Step | Call | `leaf_hash_to_index` | `key_to_index` | blob |
|---|---|---|---|---|
| 1 | `insert(k1,v1,H1)` | `{H1→idx1}` | `{k1→idx1}` | idx1=leaf(k1,H1) |
| 2 | `insert(k2,v2,H2)` | `{H1→idx1, H2→idx2}` | `{k1→idx1, k2→idx2}` | idx2=leaf(k2,H2) |
| 3 | `remove_leaf(&leaf1)` | `{H2→idx2}` | `{k2→idx2}` | unchanged |
| 4 | `add_leaf(idx1, {k1,H2})` | `{H2→idx1}` ← **H2→idx2 overwritten** | `{k1→idx1, k2→idx2}` | idx1=leaf(k1,H2) |

After step 4:
- The blob has **two leaves both carrying hash H2** (at idx1 and idx2).
- `leaf_hash_to_index` has only one entry (`H2→idx1`), so `leaf_count(2) ≠ leaf_hash_to_index_cache_length(1)`.
- `get_node_by_hash(H2)` returns `(k1, v1_new)` instead of `(k2, v2)`. [4](#0-3) 

`check_integrity` detects the length mismatch via `IntegrityLeafHashToIndexCacheLength`, but only when explicitly called — it is not invoked automatically after every `upsert`, and the corruption is already committed to the in-memory blob before any check runs. [5](#0-4) 

---

### Impact Explanation

The corrupted tree has two leaves sharing hash H2. This violates the Merkle tree's fundamental uniqueness invariant:

- `get_node_by_hash(H2)` returns the wrong `(KeyId, ValueId)` pair — the displaced leaf (k2) is effectively invisible to hash-based lookups.
- The Merkle root hash computed from this tree is wrong, because the parent chain above idx1 is dirtied and recalculated using H2 for k1, while idx2 still holds H2 for k2 — producing a root that does not correspond to any valid committed state.
- Any DataLayer proof-of-inclusion built from this corrupted root is invalid, enabling forged or unprovable state claims.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

---

### Likelihood Explanation

The `upsert` Python binding is a public API:

```python
def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
``` [6](#0-5) 

Any caller that controls `new_hash` — including a DataLayer client submitting an update — can trigger this with a hash value equal to any other leaf's hash already in the tree. No cryptographic collision is required; the attacker only needs to know (or observe) an existing leaf hash and pass it as `new_hash` for a different key's update.

---

### Recommendation

Add the same guard that `insert` uses, **before** calling `remove_leaf`, so the function rejects without mutating any state:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    // Guard: reject if new_hash already belongs to a *different* leaf
    if leaf.hash != *new_hash && self.block_status_cache.contains_leaf_hash(new_hash) {
        return Err(Error::HashAlreadyPresent());
    }

    self.block_status_cache.remove_leaf(&leaf)?;
    ...
}
```

The `leaf.hash != *new_hash` condition allows a no-op hash update (same hash, different value) to proceed without a false positive.

---

### Proof of Concept

```rust
#[test]
fn test_upsert_conflicting_hash_corrupts_cache() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    let h1 = sha256_num(&1i64);
    let h2 = sha256_num(&2i64);

    blob.insert(KeyId(1), ValueId(1), &h1, InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(2), ValueId(2), &h2, InsertLocation::Auto {}).unwrap();

    // upsert k1 with H2 — the hash already owned by k2
    let result = blob.upsert(KeyId(1), ValueId(99), &h2);

    // Should have returned Err(HashAlreadyPresent), but currently returns Ok(())
    assert!(result.is_err(), "upsert with conflicting hash must be rejected");

    // Without the fix, check_integrity reveals the corruption:
    blob.check_integrity().expect("integrity must hold after upsert");
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L372-374)
```rust
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L868-874)
```rust
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
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

**File:** wheel/python/chia_rs/datalayer.pyi (L323-323)
```text
    def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
```
