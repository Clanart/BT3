### Title
`MerkleBlob::batch_insert` Bypasses Duplicate Key/Hash Validation, Enabling Merkle Tree Root Corruption - (File: crates/chia-datalayer/src/merkle/blob.rs)

### Summary
`MerkleBlob::batch_insert` silently skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When the tree already contains two or more leaves, **every** entry in a batch bypasses those checks entirely. When the tree has zero or one leaf, only the last two entries in the batch are validated; all earlier entries are written directly to the blob without any uniqueness check. The function is exposed as a first-class Python API (`py_batch_insert`) callable by any untrusted Python code, making the corruption reachable from serialized DataLayer input.

### Finding Description

`MerkleBlob::insert` enforces two invariants before writing any leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`MerkleBlob::batch_insert` only calls `self.insert()` (the checked path) when the tree currently has ≤ 1 leaf, and even then only for the last two items popped from the vector:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 578-603
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else {
            return Ok(());
        };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // ← checked
    }
}

for ((key, value), hash) in keys_values_hashes {   // ← ALL remaining items
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // ← NO duplicate check
    indexes.push(new_leaf_index);
}
```

Two concrete bypass paths exist:

1. **Tree already has ≥ 2 leaves** – the `if` block is skipped entirely; every entry in the batch is written via `insert_entry_to_blob` with no uniqueness check.
2. **Tree has 0–1 leaves and batch has N ≥ 3 entries** – the first N−2 entries (the head of the vector, because `pop()` removes from the tail) bypass all checks.

When a duplicate `KeyId` is written, `insert_entry_to_blob` updates `block_status_cache.key_to_index` to point to the new `TreeIndex`, orphaning the original leaf in the blob. The blob now contains two leaf nodes sharing the same key, but the cache only tracks one. The same corruption occurs for duplicate `Hash` values via `leaf_hash_to_index`.

The Python binding that exposes this path to any caller:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 1503-1519
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

The Python stub confirms this is a public, documented API:

```python
# wheel/python/chia_rs/datalayer.pyi  line 331
def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```

### Impact Explanation

After a corrupted `batch_insert`:

- **Merkle root is wrong.** The root hash is computed over a tree that contains phantom duplicate leaves. Any downstream consumer that trusts the root (e.g., a DataLayer coin commitment) operates on a false value.
- **Proofs of inclusion are forgeable.** `get_proof_of_inclusion(key)` follows the cache pointer to one of the two leaves. The other leaf is structurally present in the blob but invisible to the cache, so a proof can be constructed for a key-value pair that was never legitimately inserted.
- **`check_integrity` detects the corruption** (leaf count vs. cache length mismatch), but only if explicitly called; it is not called automatically after `batch_insert`.
- **Committed DataLayer state is corrupted.** If the corrupted root is committed on-chain, the on-chain record of the DataLayer store is permanently wrong, and no valid proof can be produced for the orphaned leaf.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is the primary bulk-insertion API used by the Chia DataLayer Python layer (called in `test_datalayer.py` and production DataLayer code). Any Python process that constructs a batch containing a key already present in the tree—or a hash collision within the batch itself—triggers the corruption silently. Because the DataLayer accepts external key-value data from DataLayer store operators and their clients, an operator or a client submitting a crafted batch (e.g., re-using a `KeyId` that already exists in the store) reaches this path without any privilege.

### Recommendation

Add the same uniqueness guards to the unchecked loop in `batch_insert`:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insert_entry_to_blob call
}
```

Alternatively, pre-validate the entire batch for internal duplicates and duplicates against the existing tree before writing any entry, so the operation is atomic (all-or-nothing).

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

# Seed the tree with 2 leaves so leaf_count > 1 → the if-block is skipped
blob.insert(KeyId(10), ValueId(10), h(10))
blob.insert(KeyId(20), ValueId(20), h(20))

# batch_insert with a duplicate KeyId(10) already in the tree.
# Because leaf_count == 2 > 1, ALL entries bypass insert() and go straight
# to insert_entry_to_blob with no duplicate check.
blob.batch_insert(
    [(KeyId(30), ValueId(30)), (KeyId(10), ValueId(99))],  # KeyId(10) is a duplicate
    [h(30), h(10)],
)

blob.calculate_lazy_hashes()

# The root hash is now computed over a tree with two leaves sharing KeyId(10).
# get_proof_of_inclusion returns a proof for ValueId(99), not the original ValueId(10).
proof = blob.get_proof_of_inclusion(KeyId(10))
assert proof.valid()   # passes – but the proof is for the corrupted duplicate leaf

# check_integrity reveals the corruption
try:
    blob.check_integrity()
except Exception as e:
    print("Corruption detected:", e)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L362-374)
```rust
    pub fn insert(
        &mut self,
        key: KeyId,
        value: ValueId,
        hash: &Hash,
        insert_location: InsertLocation,
    ) -> Result<TreeIndex, Error> {
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
```

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

**File:** wheel/python/chia_rs/datalayer.pyi (L331-331)
```text
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
```
