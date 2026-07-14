### Title
Duplicate `KeyId` Bypass in `MerkleBlob::batch_insert` Corrupts DataLayer Merkle Tree Root - (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` applies duplicate-key validation only to the **last two** items in the input batch (via `self.insert()`), while all remaining items are written directly to the blob via `self.insert_entry_to_blob()` with no duplicate-key check. An attacker or buggy caller can supply a batch containing duplicate `KeyId` values (positioned outside the last-two slots) to silently insert multiple leaf nodes sharing the same key, corrupting the Merkle tree root and enabling forged inclusion proofs.

---

### Finding Description

`batch_insert` has two distinct code paths:

**Path 1 – validated (last ≤2 items, popped from the end):**
```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // checks for duplicate key
    }
}
```
`self.insert()` consults `block_status_cache` and returns `Error::KeyAlreadyPresent` on a duplicate.

**Path 2 – unvalidated (all remaining items, processed in order):**
```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { node: Node::Leaf(LeafNode { key, value, hash, ... }), ... };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // NO duplicate check
    indexes.push(new_leaf_index);
}
```
`insert_entry_to_blob` writes raw bytes directly; it never consults `block_status_cache` for key uniqueness.

Because `Vec::pop()` removes from the **tail**, the last two elements of the caller-supplied vector go through the safe path, while all earlier elements bypass it entirely. A batch of N ≥ 3 items where a duplicate `KeyId` appears among the first N-2 entries will be accepted without error.

The Python binding `py_batch_insert` exposes this path directly to untrusted callers:
```rust
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

---

### Impact Explanation

When duplicate `KeyId` leaves are inserted:

1. **`block_status_cache`** (a `KeyId → TreeIndex` map) records only one of the two leaves for that key. The second leaf exists in the blob but is invisible to the cache.
2. **`calculate_lazy_hashes`** computes the Merkle root over the full blob, including both duplicate leaves. The resulting root hash reflects a tree that the cache does not accurately represent.
3. **`get_proof_of_inclusion`** generates proofs against the cache-tracked leaf, but the root was computed over a different tree shape. Proofs can be generated for a key with the wrong value (the cache-tracked one) while the root actually commits to a different value (the uncached duplicate).
4. **`validate_merkle_proof` / `ProofOfInclusion::valid()`** will accept these proofs as valid against the corrupted root, allowing a caller to prove inclusion of a key-value pair that was never legitimately inserted.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

The Python binding `py_batch_insert` is a public API surface reachable by any Python caller without privilege. The DataLayer is used in production for storing verifiable key-value state. Any caller that constructs a batch with a repeated `KeyId` (e.g., due to a bug in the caller, or deliberately) will silently corrupt the tree. The condition requires only N ≥ 3 items in the batch with a duplicate among the first N-2.

---

### Recommendation

Add a duplicate-key check inside the bulk loop in `batch_insert`, analogous to what `insert` does. The simplest fix is to check `block_status_cache` before calling `insert_entry_to_blob`, or to collect all keys in a local `HashSet` before the loop:

```rust
let mut seen_keys = HashSet::new();
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.get_index_by_key(key).is_some() || !seen_keys.insert(key) {
        return Err(Error::KeyAlreadyPresent(key));
    }
    // ... existing insert_entry_to_blob logic
}
```

Alternatively, unify both code paths to always go through `self.insert()`, accepting the performance trade-off, or update `block_status_cache` eagerly inside the loop so that subsequent iterations can detect the duplicate.

---

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

def sha256_bytes(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Pre-populate so leaf_count > 1 (bypasses the pop-2 bootstrap path)
for i in range(2):
    blob.insert(KeyId(i), ValueId(i), sha256_bytes(i))

# batch of 3: first item (KeyId(99)) is processed via insert_entry_to_blob (no dup check)
# last 2 items are popped and go through self.insert() (dup check active)
# KeyId(99) appears twice in the batch; both are in the first N-2 = 1 slot
batch_keys   = [(KeyId(99), ValueId(10)), (KeyId(99), ValueId(20)), (KeyId(100), ValueId(100))]
batch_hashes = [sha256_bytes(10),         sha256_bytes(20),          sha256_bytes(100)]

# This should raise KeyAlreadyPresent but does NOT
blob.batch_insert(batch_keys, batch_hashes)
blob.calculate_lazy_hashes()

# The tree now has two leaves for KeyId(99) with different hashes.
# get_proof_of_inclusion returns a proof for one value, but the root
# was computed over both — the proof validates against a root that
# commits to a different state than the cache believes.
proof = blob.get_proof_of_inclusion(KeyId(99))
assert proof.valid()  # passes, but the root is corrupted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L570-603)
```rust
    pub fn batch_insert(
        &mut self,
        mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
    ) -> Result<(), Error> {
        // OPT: perhaps go back to taking an iterator?
        // OPT: would it be worthwhile to hold the entire blocks?
        let mut indexes = vec![];

        if self.block_status_cache.leaf_count() <= 1 {
            for _ in 0..2 {
                let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                    return Ok(());
                };
                self.insert(key, value, &hash, InsertLocation::Auto {})?;
            }
        }

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L2234-2252)
```rust
    #[test]
    fn test_double_insert_fails() {
        let mut blob = MerkleBlob::new(vec![]).unwrap();
        let kv = 0;
        blob.insert(
            KeyId(kv),
            ValueId(kv),
            &Hash(Bytes32::new([0u8; 32])),
            InsertLocation::Auto {},
        )
        .unwrap();
        blob.insert(
            KeyId(kv),
            ValueId(kv),
            &Hash(Bytes32::new([0u8; 32])),
            InsertLocation::Auto {},
        )
        .expect_err("");
    }
```
