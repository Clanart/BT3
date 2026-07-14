### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Guards, Corrupting Tree Root and Invalidating Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` contains a fast-path bulk-write loop that calls `insert_entry_to_blob` directly, completely skipping the `KeyAlreadyPresent` / `HashAlreadyPresent` guards that `MerkleBlob::insert` enforces. An unprivileged caller supplying a batch with a repeated `KeyId` or `Hash` silently writes duplicate leaf nodes into the blob, producing a corrupted Merkle root and invalidating every proof of inclusion derived from it.

---

### Finding Description

`MerkleBlob::insert` guards against duplicates at the very top of its body:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` has two code paths. When the tree has **≤ 1 existing leaf**, it pops the last two items from the input vector and routes them through `insert()` (which has the guards). All remaining items — and **all items when the tree already has ≥ 2 leaves** — are written via a bare `insert_entry_to_blob` call with no existence check whatsoever:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no dedup guard
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

The bootstrap branch that does use `insert()`: [3](#0-2) 

`BlockStatusCache::add_leaf` uses `HashMap::insert`, which silently overwrites the existing entry for a repeated key, so the cache ends up tracking only the last occurrence while the blob contains both leaf nodes: [4](#0-3) 

The Python binding `py_batch_insert` is the direct attacker-reachable entry point: [5](#0-4) 

Exposed in the Python type stub as `MerkleBlob.batch_insert`: [6](#0-5) 

---

### Impact Explanation

After a batch containing a duplicate key is processed:

1. The blob contains **two leaf nodes** with the same `KeyId` (and/or `Hash`).
2. The internal-node hashes built over those leaves produce a **corrupted Merkle root** — one that does not correspond to any valid set of key-value pairs.
3. `get_proof_of_inclusion` resolves the key through the cache to only one of the two leaves; the proof path it constructs does not verify against the actual root, so **every proof of inclusion is invalid**.
4. `get_node_by_hash` and `get_keys_values` return inconsistent results because the cache and the blob disagree.
5. Any DataLayer delta or sync operation built on top of the corrupted blob propagates the wrong root to peers, enabling **forged inclusion proofs** or **false exclusion** of legitimately inserted keys.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

The Python `MerkleBlob.batch_insert` API is a public, unprivileged interface. Any caller — including application code that assembles a batch from user-supplied data — can trigger this by passing a list containing a repeated `(KeyId, ValueId)` pair. No special privilege, key material, or network access is required. The bug is silent: `batch_insert` returns `Ok(())` and the corruption is only detectable by calling `check_integrity()` explicitly, which is not done automatically in production paths.

---

### Recommendation

Add the same existence checks at the top of the fast-path loop in `batch_insert` that `insert` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing fast-path write ...
}
```

Alternatively, pre-validate the entire input vector for uniqueness before any writes begin, so the tree is never left in a partially-written corrupt state.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(bytearray())

# Seed with 2 leaves so leaf_count > 1 → ALL batch items take the fast path
blob.insert(KeyId(100), ValueId(100), h(100))
blob.insert(KeyId(101), ValueId(101), h(101))

# Batch with a duplicate key (KeyId(1) appears twice)
# Both entries bypass the KeyAlreadyPresent guard
keys_values = [(KeyId(1), ValueId(1)), (KeyId(2), ValueId(2)), (KeyId(1), ValueId(99))]
hashes      = [h(1),                   h(2),                   h(999)]

blob.batch_insert(keys_values, hashes)   # returns Ok — no error raised

# Tree root is now corrupted; proof for KeyId(1) will not verify
blob.calculate_lazy_hashes()
proof = blob.get_proof_of_inclusion(KeyId(1))
print("proof valid:", proof.valid())   # False — root mismatch
```

The duplicate `KeyId(1)` in the batch is silently accepted, two leaf nodes with the same key are written into the blob, the Merkle root is wrong, and the proof of inclusion fails to verify.

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-585)
```rust
        if self.block_status_cache.leaf_count() <= 1 {
            for _ in 0..2 {
                let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                    return Ok(());
                };
                self.insert(key, value, &hash, InsertLocation::Auto {})?;
            }
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L587-603)
```rust
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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
