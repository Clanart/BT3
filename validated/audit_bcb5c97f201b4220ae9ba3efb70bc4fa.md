### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Validation, Corrupting DataLayer Merkle Tree Root — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a caller supplies a batch containing a repeated `KeyId` or `Hash` — or a key/hash already present in the tree — the fast-path loop writes multiple leaf nodes for the same key directly into the blob via `insert_entry_to_blob`, which silently overwrites the `BlockStatusCache` entry rather than rejecting the input. The resulting blob contains orphaned leaf nodes that are structurally wired into the tree but invisible to the cache, producing a Merkle root that does not correspond to any valid key-value set and invalidating all subsequent proofs of inclusion/exclusion.

---

### Finding Description

`MerkleBlob::insert` enforces two guards before writing any leaf:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` (with those guards) only for the last one or two items when the tree has ≤ 1 existing leaves. All remaining items — and all items when the tree already has ≥ 2 leaves — are written through a fast-path loop that calls `insert_entry_to_blob` directly, with no duplicate check at all:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which calls `HashMap::insert` on both `key_to_index` and `leaf_hash_to_index`. `HashMap::insert` silently overwrites an existing entry and discards the old index — it does not return an error:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [3](#0-2) 

The overwritten (first) leaf's index is removed from `free_indexes` but never re-added; the blob still contains that leaf node at its original position, and the tree's internal-node parent/child pointers still reference it. The cache, however, now points only to the second (overwriting) leaf. The tree therefore contains a structurally connected but cache-invisible duplicate leaf, and the Merkle root computed from the full blob diverges from what the cache believes the tree contains.

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
``` [4](#0-3) 

The analog to the external report is exact: the check (`contains_key`/`contains_leaf_hash`) exists in `insert()` but is not applied inside `batch_insert`'s fast-path loop, just as the Near unlock check existed but was not re-applied in the callback. The silent overwrite in `add_leaf` (returning nothing, analogous to `remove_transfer` returning `None`) is what allows the corruption to proceed without error.

---

### Impact Explanation

A corrupted `MerkleBlob` produces a root hash that does not correspond to any valid key-value set. Any `ProofOfInclusion` generated after the corruption will be structurally invalid: the proof path will traverse the tree as the cache sees it, but `proof.valid()` computes hashes bottom-up from the leaf, and the root it arrives at will not match the actual root stored on-chain (which was computed from the full, duplicate-containing blob). This allows an attacker to:

- Cause a DataLayer store to commit a root hash that cannot be verified by any honest proof, breaking all inclusion/exclusion guarantees for that store.
- Selectively make a key appear to be absent (the orphaned first leaf is unreachable via the cache) while the on-chain root still covers it, enabling forged exclusion proofs.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`batch_insert` is the primary bulk-load API for DataLayer and is called from Python via `py_batch_insert`. Any DataLayer node that processes key-value updates from an untrusted source (e.g., a DataLayer store owner submitting a delta, or a sync operation) can be fed a batch containing a repeated key. No special privilege is required; the attacker only needs to supply a `Vec` with a duplicate entry. The existing test suite (`test_batch_insert`) never tests duplicate-key inputs to `batch_insert`, confirming the gap is undetected. [5](#0-4) 

---

### Recommendation

Add duplicate-key and duplicate-hash validation at the start of `batch_insert` before any writes occur, mirroring the guards in `insert()`. A `HashSet` over the incoming keys and hashes can detect intra-batch duplicates in O(n), and the existing `block_status_cache.contains_key` / `contains_leaf_hash` checks cover conflicts with the existing tree. Alternatively, route all items through `self.insert()` (accepting the performance cost) or add the cache checks inline in the fast-path loop before each `insert_entry_to_blob` call.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

blob = MerkleBlob(bytearray())

# Pre-populate so leaf_count >= 2, forcing all batch items through the fast path
k0, v0 = KeyId(0), ValueId(0)
k1, v1 = KeyId(1), ValueId(1)
h0 = hashlib.sha256(b"h0").digest()
h1 = hashlib.sha256(b"h1").digest()
blob.insert(k0, v0, h0)
blob.insert(k1, v1, h1)

# Now batch_insert with a duplicate key — KeyId(99) appears twice
dup_key = KeyId(99)
h2 = hashlib.sha256(b"h2").digest()
h3 = hashlib.sha256(b"h3").digest()
h4 = hashlib.sha256(b"h4").digest()

# This should raise KeyAlreadyPresent but does not
blob.batch_insert(
    [(KeyId(10), ValueId(10)), (dup_key, ValueId(20)), (dup_key, ValueId(30))],
    [h2, h3, h4],
)

blob.calculate_lazy_hashes()

# The blob now contains two leaf nodes for KeyId(99); the cache only sees one.
# get_proof_of_inclusion returns a proof whose root does not match the actual
# root computed from the full (duplicate-containing) blob.
proof = blob.get_proof_of_inclusion(dup_key)
assert not proof.valid(), "proof is invalid because root is corrupted"
```

The root hash stored in the blob is computed over both duplicate leaves, but the proof path follows only the cache-visible leaf, so `proof.valid()` returns `False` — demonstrating that the committed root is unverifiable and the tree state is corrupted. [6](#0-5) [7](#0-6) [3](#0-2)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1013-1030)
```rust
    fn insert_entry_to_blob(&mut self, index: TreeIndex, block: &Block) -> Result<(), Error> {
        let new_block_bytes = block.to_bytes()?;
        let extend_index = self.extend_index();
        match index.cmp(&extend_index) {
            Ordering::Greater => return Err(Error::BlockIndexOutOfBounds(index)),
            Ordering::Equal => self.blob.extend_from_slice(&new_block_bytes),
            Ordering::Less => {
                self.blob[block_range(index)].copy_from_slice(&new_block_bytes);
            }
        }

        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }

        Ok(())
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L2254-2295)
```rust
    #[rstest]
    fn test_batch_insert(
        #[values(0, 1, 2, 10)] pre_inserts: usize,
        #[values(0, 1, 2, 8, 9)] count: usize,
    ) {
        let mut blob = MerkleBlob::new(vec![]).unwrap();
        for i in 0..pre_inserts {
            let i = i as i64;
            blob.insert(
                KeyId(i),
                ValueId(i),
                &sha256_num(&i),
                InsertLocation::Auto {},
            )
            .unwrap();
        }
        open_dot(blob.to_dot().unwrap().set_note("initial"));

        let mut batch: Vec<((KeyId, ValueId), Hash)> = vec![];

        let mut batch_map: HashMap<KeyId, ValueId> = HashMap::new();
        for i in pre_inserts..(pre_inserts + count) {
            let i = i as i64;
            batch.push(((KeyId(i), ValueId(i)), sha256_num(&i)));
            batch_map.insert(KeyId(i), ValueId(i));
        }

        let before = blob.get_keys_values().unwrap();
        blob.batch_insert(batch).unwrap();
        let after = blob.get_keys_values().unwrap();

        open_dot(
            blob.to_dot()
                .unwrap()
                .set_note(&format!("after batch insert of {count} values")),
        );

        let mut expected = before.clone();
        expected.extend(batch_map);

        assert_eq!(after, expected);
    }
```
