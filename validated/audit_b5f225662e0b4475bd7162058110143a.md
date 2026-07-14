Looking at the `batch_insert` path in `MerkleBlob` and how it handles duplicate keys vs. the single `insert` path.

**Key finding:** `batch_insert` skips duplicate-key/hash enforcement for all items beyond the first two, directly calling `insert_entry_to_blob` → `add_leaf` which silently overwrites the `block_status_cache` HashMap entry for a duplicate `KeyId`. This corrupts the Merkle blob: the orphaned duplicate leaf is written into the raw bytes but is no longer tracked, producing an incorrect Merkle root and a blob that cannot be reloaded from disk.

---

### Title
`batch_insert` Bypasses Duplicate-Key Enforcement, Silently Corrupting the DataLayer Merkle Blob Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` omits the duplicate `KeyId` and leaf-hash checks that `MerkleBlob::insert` enforces. When a caller supplies a batch of ≥3 entries containing a repeated `KeyId`, the extra leaf is written into the raw blob bytes without error, the `block_status_cache` silently overwrites the earlier entry, and the resulting Merkle root is computed over a structurally inconsistent tree. The corrupted blob cannot be reloaded from disk, and any root hash derived from it is invalid.

### Finding Description

`MerkleBlob::insert` guards against duplicates at lines 369–374:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` calls `self.insert(…)` only for the first two items (when the tree has ≤1 existing leaves). Every subsequent item in the batch is written directly via `self.insert_entry_to_blob(new_leaf_index, &new_block)` with no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    // ... build block ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // no key/hash check
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` calls `add_leaf`, which does:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);          // silently overwrites
    self.leaf_hash_to_index.insert(leaf.hash, index);   // silently overwrites
}
```

A `HashMap::insert` with a pre-existing key silently replaces the old entry. The earlier duplicate leaf node remains in the raw blob bytes at its original `TreeIndex`, but the cache no longer points to it. The tree-building loop then computes internal-node hashes over this inconsistent structure, producing a wrong Merkle root.

When the blob is later persisted via `to_path` and reloaded via `from_path` → `MerkleBlob::new` → `BlockStatusCache::new`, the traversal encounters both leaf nodes with the same `KeyId` and returns `Err(Error::KeyAlreadyPresent())`, making the blob permanently unloadable.

`check_integrity_on_drop` is `false` in production (`cfg!(test)` is only true in test builds), so the corruption is not caught at runtime.

### Impact Explanation

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

- The Merkle root stored in the blob is computed over a tree containing an orphaned duplicate leaf, making it incorrect relative to the actual key-value set.
- Any downstream consumer that trusts this root (e.g., for DataLayer inclusion proofs) will accept a proof against a wrong root.
- The blob becomes permanently unloadable from disk after the first persist/reload cycle, constituting committed state corruption.
- `get_proof_of_inclusion` for the duplicate key returns a proof anchored to the wrong leaf (the last-inserted one), while the first leaf's data is silently lost.

### Likelihood Explanation

`batch_insert` is a public API exposed to Python callers via `py_batch_insert`. Any caller that constructs a batch with a repeated `KeyId` — whether by mistake or maliciously — triggers the bug. No privileged role is required. The Python binding accepts an arbitrary list of `(KeyId, ValueId, Hash)` tuples with no pre-validation.

### Recommendation

Add duplicate-key and duplicate-hash checks at the start of the bulk loop in `batch_insert`, mirroring the guards in `insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insertion logic ...
}
```

Alternatively, pre-validate the entire batch for uniqueness before any blob mutation begins, so the blob is never left in a partially-written inconsistent state.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import os

blob = MerkleBlob(blob=bytearray())

# Build a batch of 4 entries where KeyId(1) appears twice (positions 0 and 2 after pop())
# batch_insert pops last two for self.insert(), then iterates the rest without checks
batch = [
    (KeyId(10), ValueId(10), os.urandom(32)),
    (KeyId(20), ValueId(20), os.urandom(32)),
    (KeyId(1),  ValueId(99), os.urandom(32)),   # duplicate key
    (KeyId(1),  ValueId(1),  os.urandom(32)),   # first occurrence (popped by insert())
]
# The last two are popped and inserted via self.insert() (with checks).
# The first two are inserted via insert_entry_to_blob() without checks.
# KeyId(1) appears in both halves → duplicate written silently.

blob.batch_insert([(kv[0], kv[1]) for kv in batch], [kv[2] for kv in batch])
blob.calculate_lazy_hashes()

# Root is now computed over a structurally inconsistent tree.
root = blob.get_root_hash()
print(f"Corrupted root: {root}")

# Persist and reload — will raise KeyAlreadyPresentError
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    p = pathlib.Path(d) / "blob"
    blob.to_path(p)
    try:
        MerkleBlob.from_path(p)   # raises KeyAlreadyPresent
    except Exception as e:
        print(f"Reload failed (blob permanently corrupted): {e}")
```

**Root cause lines:** [1](#0-0) 

Duplicate check present in `insert` but absent in the bulk loop of `batch_insert`: [2](#0-1) 

Silent HashMap overwrite in `add_leaf`: [3](#0-2) 

`BlockStatusCache::new` detects the duplicate on reload but only after the blob is already persisted: [4](#0-3) 

`check_integrity_on_drop` is `false` in production, so no runtime guard catches this: [5](#0-4)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L109-114)
```rust
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
                if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
                    return Err(Error::HashAlreadyPresent());
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L327-329)
```rust
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L587-602)
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
```
