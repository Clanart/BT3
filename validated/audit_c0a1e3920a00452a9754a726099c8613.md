### Title
`upsert` Missing Hash-Collision Check Corrupts `leaf_hash_to_index` Cache — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::upsert` does not check whether the supplied `new_hash` is already stored as the leaf hash of a **different** key before overwriting the cache. An attacker with write access to a DataLayer store can call `upsert(key_A, value, hash_of_key_B)`, silently overwriting key_B's entry in `leaf_hash_to_index` with key_A's index, breaking the bijection invariant and causing `get_node_by_hash(hash_of_key_B)` to return key_A's data.

---

### Finding Description

**Root cause — `upsert` update path skips the hash-uniqueness guard**

`insert` correctly guards against duplicate hashes: [1](#0-0) 

But the update branch of `upsert` (key already present) performs no such check: [2](#0-1) 

The sequence is:
1. `remove_leaf(&leaf)` removes the **old** hash (H_A) from `leaf_hash_to_index`.
2. `leaf.hash = new_hash` (H_B — key_B's hash).
3. `insert_entry_to_blob` → `add_leaf` unconditionally calls `HashMap::insert(H_B, idx_A)`. [3](#0-2) 

`add_leaf` has no collision check: [4](#0-3) 

`HashMap::insert` silently overwrites the existing `H_B → idx_B` entry with `H_B → idx_A`.

**Cache state after the attack (2-leaf tree, key_A at idx_A, key_B at idx_B):**

| Step | `leaf_hash_to_index` |
|---|---|
| Initial | `{H_A: idx_A, H_B: idx_B}` |
| After `remove_leaf(leaf_A)` | `{H_B: idx_B}` |
| After `add_leaf(idx_A, leaf{hash=H_B})` | `{H_B: idx_A}` ← key_B's entry gone |

**Blob corruption:** key_A's leaf node in the raw blob now stores hash H_B, identical to key_B's leaf. The tree contains two leaves with the same hash — a fundamental Merkle invariant violation.

**`get_node_by_hash` returns wrong data:** [5](#0-4) 

After the attack, `get_node_by_hash(H_B)` resolves `idx_A` from the cache and returns key_A's `(key, value)` instead of key_B's.

**`check_integrity` would detect this** (leaf_count=2 but `leaf_hash_to_index.len()`=1): [6](#0-5) 

But `check_integrity_on_drop` is only enabled in test builds: [7](#0-6) 

In production the corruption is silent.

---

### Impact Explanation

- `get_node_by_hash(H_B)` returns key_A's `(KeyId, ValueId)` — hash-to-key lookup is wrong.
- `leaf_hash_to_index` is no longer a bijection; key_B's hash is unmapped.
- The raw blob has two leaves with identical hashes, corrupting the Merkle tree structure and any root hash derived from it.
- State proofs generated from the corrupted tree are invalid, enabling forged or misleading inclusion proofs for DataLayer state.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic … corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

Any caller with write access to a DataLayer store (the normal DataLayer write-permission model) can trigger this with a single `upsert` call. No privileged role, leaked key, or network-level attack is required. The attacker only needs to know the hash of an existing leaf (readable via `get_node_by_hash` or `get_proof_of_inclusion`).

---

### Recommendation

Add a hash-collision check in the update branch of `upsert`, mirroring the guard already present in `insert`:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    // NEW: reject if new_hash is already used by a different key
    if leaf.hash != *new_hash && self.block_status_cache.contains_leaf_hash(new_hash) {
        return Err(Error::HashAlreadyPresent());
    }

    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    leaf.value = value;
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?;
    // ...
}
```

---

### Proof of Concept

```rust
#[test]
fn test_upsert_hash_collision_corrupts_cache() {
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    let key_a = KeyId(1);
    let key_b = KeyId(2);
    let hash_a = Hash::from([0xAA; 32]);
    let hash_b = Hash::from([0xBB; 32]);

    blob.insert(key_a, ValueId(10), &hash_a, InsertLocation::Auto {}).unwrap();
    blob.insert(key_b, ValueId(20), &hash_b, InsertLocation::Auto {}).unwrap();

    // Attacker calls upsert(key_A, ..., hash_B) — hash_B belongs to key_B
    blob.upsert(key_a, ValueId(99), &hash_b).unwrap(); // should error, but doesn't

    // Cache is now corrupted: get_node_by_hash(hash_b) returns key_A's data
    let (returned_key, _) = blob.get_node_by_hash(hash_b).unwrap();
    assert_eq!(returned_key, key_b, "FAIL: returned key_A instead of key_B");
}
```

This test will fail at the final assertion, demonstrating that `get_node_by_hash(hash_b)` returns `key_a` instead of `key_b` after the corrupting `upsert`.

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-193)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L328-329)
```rust
            check_integrity_on_drop: cfg!(test),
        };
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
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
