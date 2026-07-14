Let me read the key sections more carefully to confirm the exact flow.

### Title
Missing `HashAlreadyPresent` Guard in `upsert` Allows Duplicate Leaf Hash, Corrupting Merkle Root and Enabling Forged Inclusion Proofs — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::upsert` updates an existing leaf's hash without first checking whether the new hash is already held by a different leaf. Because `remove_leaf` removes the old hash before `add_leaf` inserts the new one, the `HashAlreadyPresent` guard that exists in `insert` is never reached. The result is two blob-resident leaves sharing the same hash, a silently corrupted `leaf_hash_to_index` cache, a wrong Merkle root, and a broken `get_node_by_hash` lookup — all detectable by `check_integrity`.

---

### Finding Description

`insert` guards against duplicate leaf hashes: [1](#0-0) 

`upsert`'s update path has no equivalent guard: [2](#0-1) 

The sequence inside `upsert` when the key already exists is:

1. **Line 798** — `remove_leaf(&leaf)` removes the old hash H1 from `leaf_hash_to_index` and the key from `key_to_index`. [3](#0-2) 

2. **Line 803** — `insert_entry_to_blob` unconditionally calls `add_leaf`, which does a bare `HashMap::insert` for the new hash H2 — no existence check, no error on collision. [4](#0-3) [5](#0-4) 

If H2 is already the hash of leaf B, `add_leaf` silently overwrites `leaf_hash_to_index[H2]` from leaf B's index to leaf A's index. Leaf B still exists in the blob with hash H2, but its hash is no longer tracked in the cache.

---

### Impact Explanation

After the attack the tree is in an inconsistent state:

- **Two blob leaves share hash H2.** The Merkle root computed from them is wrong.
- **`leaf_hash_to_index` maps H2 to leaf A's index only.** Leaf B's hash entry is silently gone.
- **`get_node_by_hash(H2)`** returns leaf A's `(key, value)` — a wrong answer for any verifier querying leaf B's hash.
- **`check_integrity` detects the corruption** via the invariant `leaf_count != leaf_hash_to_index_cache_length`: [6](#0-5) 
- A proof of inclusion built for leaf B (`get_proof_of_inclusion(keyB)`) uses hash H2 as the leaf hash, but H2 is also the hash of leaf A — enabling a forged cross-key inclusion claim.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

The hash is a caller-supplied parameter to `upsert`. Any code path that calls `upsert` with a hash equal to an existing leaf's hash triggers the bug. No cryptographic collision is required — the attacker only needs to observe the current tree state (public in a blockchain context) and pass a known leaf hash as the `new_hash` argument. The call sequence is three lines of Rust and is locally testable without any privileged access.

---

### Recommendation

Add a duplicate-hash check at the top of the update branch in `upsert`, mirroring the guard already present in `insert`:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    // Guard: reject if new_hash is already held by a *different* leaf
    if *new_hash != leaf.hash && self.block_status_cache.contains_leaf_hash(new_hash) {
        return Err(Error::HashAlreadyPresent());
    }

    self.block_status_cache.remove_leaf(&leaf)?;
    leaf.hash.clone_from(new_hash);
    leaf.value = value;
    block.node = Node::Leaf(leaf);
    self.insert_entry_to_blob(leaf_index, &block)?;
    ...
}
```

The `*new_hash != leaf.hash` condition allows a no-op re-upsert with the same hash (idempotent update) while blocking any cross-leaf hash collision.

---

### Proof of Concept

```rust
#[test]
fn test_upsert_hash_collision_corrupts_tree() {
    use crate::{Hash, KeyId, MerkleBlob, ValueId};
    use crate::merkle::blob::InsertLocation;

    let h1 = Hash::from([1u8; 32]);
    let h2 = Hash::from([2u8; 32]);
    let key_a = KeyId(1);
    let key_b = KeyId(2);

    let mut blob = MerkleBlob::new(vec![]).unwrap();

    // Insert leaf A (hash H1) and leaf B (hash H2)
    blob.insert(key_a, ValueId(10), &h1, InsertLocation::Auto {}).unwrap();
    blob.insert(key_b, ValueId(20), &h2, InsertLocation::Auto {}).unwrap();

    // Upsert leaf A with H2 — same hash as leaf B
    // Expected: HashAlreadyPresent error
    // Actual (buggy): succeeds silently
    blob.upsert(key_a, ValueId(99), &h2).unwrap();

    // Tree is now corrupt:
    // - check_integrity fails (leaf_count=2, leaf_hash_to_index len=1)
    blob.check_integrity_on_drop = false;
    assert!(blob.check_integrity().is_err(), "integrity should fail");

    // - get_node_by_hash(H2) returns key_a, not key_b
    let (returned_key, _) = blob.get_node_by_hash(h2).unwrap();
    assert_ne!(returned_key, key_b, "wrong key returned for leaf B hash");
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L199-207)
```rust
    fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
        let Some(index) = self.key_to_index.remove(&node.key) else {
            return Err(Error::UnknownKey(node.key));
        };
        self.leaf_hash_to_index.remove(&node.hash);

        self.free_indexes.insert(index);

        Ok(())
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L868-873)
```rust
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1026)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
```
