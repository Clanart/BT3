### Title
`MerkleBlob::batch_insert` Silently Accepts Duplicate Keys/Hashes, Corrupting DataLayer Merkle Tree Root - (File: crates/chia-datalayer/src/merkle/blob.rs)

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that `MerkleBlob::insert` enforces for all items beyond the first two in a batch. An untrusted caller can supply a batch containing repeated `KeyId` or `Hash` values; the duplicates are written directly into the blob, producing a structurally corrupt Merkle tree whose root hash is wrong and whose proofs of inclusion are invalid or forgeable.

### Finding Description

`MerkleBlob::insert` enforces two invariants before writing any leaf:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [1](#0-0) 

`batch_insert` pops the **last two** items from the input vector and routes them through `insert` (with the guards active) only when the tree has ≤ 1 existing leaf:

```rust
if self.block_status_cache.leaf_count() <= 1 {
    for _ in 0..2 {
        let Some(((key, value), hash)) = keys_values_hashes.pop() else { ... };
        self.insert(key, value, &hash, InsertLocation::Auto {})?;
    }
}
``` [2](#0-1) 

All **remaining** items (the front of the vector, i.e. every item beyond the last two) are written directly to the blob via `insert_entry_to_blob` with **no duplicate check**:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ... node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

The fast-path subtree is then attached to the existing tree without any post-hoc uniqueness validation: [4](#0-3) 

The Python binding `py_batch_insert` passes caller-supplied lists directly into `batch_insert` with no pre-filtering: [5](#0-4) 

The `BlockStatusCache` maps each `KeyId` to exactly one `TreeIndex` via a `HashMap`. When two leaves share the same `KeyId`, the second `HashMap::insert` silently overwrites the first, leaving one leaf unreachable through the cache while both leaves remain in the blob and contribute to internal-node hash computations. [6](#0-5) 

### Impact Explanation

A batch containing a duplicate `KeyId` (or duplicate `Hash`) produces a tree where:

1. **Root hash is wrong**: two leaf nodes with the same key contribute to different internal-node hashes, so the computed root does not correspond to any valid set of key-value pairs.
2. **Proofs of inclusion are invalid**: `get_proof_of_inclusion` follows the cache pointer to one of the two leaves; the other leaf is invisible to the cache but still alters every ancestor hash, so the proof path does not reconstruct the actual root.
3. **Forged exclusion proofs**: a key that is genuinely present in the tree can be made to appear absent (or vice versa) by inserting a duplicate that shifts the cache pointer.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."* [7](#0-6) 

### Likelihood Explanation

The Python binding `py_batch_insert` is a public API surface reachable by any caller that constructs a `MerkleBlob`. A DataLayer client that assembles a batch from an untrusted or buggy source (e.g., a list of key-value deltas that accidentally repeats a key) will silently corrupt its own tree. No privileged role or key material is required; only the ability to call `batch_insert` with a list of ≥ 3 entries containing a repeated key or hash. [5](#0-4) 

### Recommendation

Add duplicate-key and duplicate-hash checks at the start of `batch_insert`, mirroring the guards in `insert`:

```rust
pub fn batch_insert(
    &mut self,
    mut keys_values_hashes: Vec<((KeyId, ValueId), Hash)>,
) -> Result<(), Error> {
    // Reject duplicates within the batch itself
    let mut seen_keys = HashSet::new();
    let mut seen_hashes = HashSet::new();
    for ((key, _), hash) in &keys_values_hashes {
        if self.block_status_cache.contains_key(*key) || !seen_keys.insert(*key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) || !seen_hashes.insert(*hash) {
            return Err(Error::HashAlreadyPresent());
        }
    }
    // ... rest of existing logic
}
``` [8](#0-7) 

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

let mut blob = MerkleBlob::new(vec![]).unwrap();

// Pre-populate with 2 leaves so batch_insert takes the fast path for items 3+
for i in 0i64..2 {
    blob.insert(KeyId(i), ValueId(i),
        &Hash(Bytes32::new([i as u8; 32])),
        InsertLocation::Auto {}).unwrap();
}

// Batch with a duplicate key (KeyId(99) appears twice)
let dup_hash_a = Hash(Bytes32::new([99u8; 32]));
let dup_hash_b = Hash(Bytes32::new([100u8; 32]));
let batch = vec![
    ((KeyId(99), ValueId(1)), dup_hash_a),  // first occurrence
    ((KeyId(99), ValueId(2)), dup_hash_b),  // duplicate key — should be rejected
    ((KeyId(10), ValueId(10)), Hash(Bytes32::new([10u8; 32]))),
];

// Currently succeeds — no error is returned
blob.batch_insert(batch).unwrap();

// Root hash is now computed over a tree with two leaves sharing KeyId(99),
// making it inconsistent with any valid key-value set.
// check_integrity() will detect the cache/blob mismatch.
blob.check_integrity().expect_err("tree is corrupt");
``` [7](#0-6) [9](#0-8)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L96-130)
```rust
impl BlockStatusCache {
    fn new(blob: &[u8]) -> Result<Self, Error> {
        let index_count = blob.len() / BLOCK_SIZE;

        let mut seen_indexes: BitVec<u64, bitvec::order::Lsb0> = BitVec::repeat(false, index_count);
        let mut key_to_index: HashMap<KeyId, TreeIndex> = HashMap::default();
        let mut leaf_hash_to_index: HashMap<Hash, TreeIndex> = HashMap::default();

        for item in LeftChildFirstIterator::new(blob, None) {
            let (index, block) = item?;
            seen_indexes.set(index.0 as usize, true);

            if let Node::Leaf(leaf) = block.node {
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
                if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
                    return Err(Error::HashAlreadyPresent());
                }
            }
        }

        let mut free_indexes: IndexSet<TreeIndex> = IndexSet::new();
        for (index, seen) in seen_indexes.iter().enumerate() {
            if !seen {
                free_indexes.insert(TreeIndex(index as u32));
            }
        }

        Ok(Self {
            free_indexes,
            key_to_index,
            leaf_hash_to_index,
        })
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L650-656)
```rust
        if indexes.len() == 1 {
            // OPT: can we avoid this extra min height leaf traversal?
            let min_height_leaf = self.get_min_height_leaf()?;
            self.insert_subtree_at_key(min_height_leaf.key, indexes[0], Side::Left)?;
        }

        Ok(())
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
