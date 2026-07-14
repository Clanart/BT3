### Title
`MerkleBlob::batch_insert` Bypasses Duplicate-Key/Hash Validation, Corrupting Tree Root and Invalidating Proofs of Inclusion — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` skips the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces. When a batch containing a repeated `KeyId` or `Hash` is submitted (including keys already present in the tree), duplicate leaf nodes are silently written into the blob. The `block_status_cache` silently overwrites its index mapping for the colliding key, leaving an orphaned leaf in the raw blob that is invisible to the cache but still participates in hash computation. The resulting Merkle root is computed over a structurally inconsistent tree, and any proof of inclusion generated afterward is invalid against that root.

---

### Finding Description

`MerkleBlob::insert` enforces two guards before writing a leaf:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 369-374
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` calls `self.insert()` (with those guards) only for the first two items when the tree has ≤ 1 existing leaf. Every other item is written directly via `insert_entry_to_blob`, which performs **no** duplicate check:

```rust
// crates/chia-datalayer/src/merkle/blob.rs  lines 578-603
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;  // guarded
    }
}
for ((key, value), hash) in keys_values_hashes {          // ALL remaining items
    ...
    self.insert_entry_to_blob(new_leaf_index, &new_block)?; // NO duplicate check
    indexes.push(new_leaf_index);
}
``` [2](#0-1) 

`insert_entry_to_blob` calls `block_status_cache.add_leaf`, which uses `HashMap::insert` — silently overwriting the existing index mapping for a colliding key:

```rust
// lines 1024-1027
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    ...
}
``` [3](#0-2) 

```rust
// lines 188-192
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index);
}
``` [4](#0-3) 

After the call returns:
- The raw blob contains **two** leaf nodes sharing the same `KeyId`.
- The cache tracks only the last-written one; the first is orphaned but still reachable by tree traversal.
- `calculate_lazy_hashes()` walks the full blob tree (including the orphaned duplicate) and produces a root hash that incorporates both leaves.
- `get_proof_of_inclusion` follows the cache to the second leaf and builds a Merkle path that does **not** match the actual root hash, so `proof.valid()` returns `false`.

The Python binding `py_batch_insert` exposes this path directly to callers:

```rust
// lines 1503-1519
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> { ... self.batch_insert(zip(keys_values, hashes).collect())?; ... }
``` [5](#0-4) 

---

### Impact Explanation

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic corrupts tree roots or lets untrusted input prove invalid state.**

Concretely:
- A caller (local or via delta-sync from a peer) submitting a batch with a repeated key silently corrupts the `MerkleBlob`.
- The stored root hash diverges from what any honest proof-of-inclusion path can verify.
- Downstream consumers that rely on `proof.valid()` against the stored root will see all proofs fail, or — if the root is recomputed from the cache rather than the blob — will accept a root that does not commit to the full blob contents, enabling forged inclusion/exclusion claims. [6](#0-5) 

---

### Likelihood Explanation

The `batch_insert` API is the primary bulk-write path for the DataLayer. Any delta-sync message from a peer, or any application layer that assembles a batch without pre-deduplicating keys, can trigger this. No special privilege is required — only the ability to supply a `Vec<((KeyId, ValueId), Hash)>` with a repeated key. The Python binding makes this reachable from all Python-level DataLayer code.

---

### Recommendation

Add the same duplicate guards at the top of `batch_insert` (or inside the fast path loop) that `insert` already enforces:

```rust
pub fn batch_insert(&mut self, keys_values_hashes: Vec<((KeyId, ValueId), Hash)>) -> Result<(), Error> {
    // Pre-validate the entire batch before mutating any state
    for ((key, _value), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // Also check for intra-batch duplicates
    ...
    // existing logic follows
}
```

Alternatively, route every item through `self.insert()` (accepting the performance cost), or perform a single O(n) deduplication pass over the input before any blob mutation.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Seed the tree with 2 leaves so leaf_count > 1 — all batch items bypass insert()
blob.insert(KeyId(0), ValueId(0), &Hash(Bytes32::new([0u8; 32])), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(1), ValueId(1), &Hash(Bytes32::new([1u8; 32])), InsertLocation::Auto {}).unwrap();

// Batch with a duplicate key (KeyId(0) already present)
let batch = vec![
    ((KeyId(0), ValueId(99)), Hash(Bytes32::new([99u8; 32]))),  // duplicate key — no error!
    ((KeyId(2), ValueId(2)),  Hash(Bytes32::new([2u8;  32]))),
];
blob.batch_insert(batch).unwrap();  // succeeds silently

blob.calculate_lazy_hashes().unwrap();

// The proof is now invalid against the corrupted root
let proof = blob.get_proof_of_inclusion(KeyId(0)).unwrap();
assert!(!proof.valid());  // proof fails — tree root is corrupted
``` [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L40-58)
```rust
    pub fn valid(&self) -> bool {
        let mut existing_hash = self.node_hash;

        for layer in &self.layers {
            let calculated_hash = crate::calculate_internal_hash(
                &existing_hash,
                layer.other_hash_side,
                &layer.other_hash,
            );

            if calculated_hash != layer.combined_hash {
                return false;
            }

            existing_hash = calculated_hash;
        }

        existing_hash == self.root_hash()
    }
```
