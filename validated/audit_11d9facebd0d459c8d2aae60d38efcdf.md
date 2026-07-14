Tracing the exact code path carefully:

**`upsert` (lines 792–810):** [1](#0-0) 

**`remove_leaf` (lines 199–208):** removes `hash_A` from `leaf_hash_to_index` and marks `index_A` as free. [2](#0-1) 

**`insert_entry_to_blob` → `add_leaf` (lines 188–193):** unconditionally calls `leaf_hash_to_index.insert(leaf.hash, index)`, which silently overwrites any existing entry for `hash_B`. [3](#0-2) [4](#0-3) 

**`insert` has the guard; `upsert` does not:** [5](#0-4) 

`insert` explicitly rejects a hash already present in the cache. `upsert`'s update path has no equivalent check before calling `remove_leaf` + `insert_entry_to_blob`.

**Resulting state after `upsert(key_A, value, hash_B)`:**

| Structure | Before | After |
|---|---|---|
| blob @ index_A | leaf(key_A, hash_A) | leaf(key_A, **hash_B**) |
| blob @ index_B | leaf(key_B, hash_B) | leaf(key_B, hash_B) — unchanged |
| `leaf_hash_to_index[hash_A]` | index_A | **removed** |
| `leaf_hash_to_index[hash_B]` | index_B | **overwritten → index_A** |

**`get_node_by_hash` (lines 1198–1208)** trusts the cache exclusively: [6](#0-5) 

After the corruption, `get_node_by_hash(hash_B)` reads `index_A` from the cache and returns `(key_A, new_value)` instead of `(key_B, value_B)`.

**`check_just_integrity` would detect this** (leaf_count=2 vs leaf_hash_to_index length=1): [7](#0-6) 

However, `check_integrity_on_drop` is only enabled in test builds (`cfg!(test)`): [8](#0-7) 

In production, no automatic integrity check runs after `upsert`.

Additionally, the blob now contains two leaves with identical hash `hash_B`, which corrupts the Merkle root (internal node hashes are computed from leaf hashes). Any root hash computed after this point is invalid.

---

### Title
`MerkleBlob::upsert` Missing Duplicate-Hash Guard Corrupts `leaf_hash_to_index` Cache and Merkle Root — (`crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::upsert` does not check whether `new_hash` is already present in `leaf_hash_to_index` for a different key before updating a leaf. This allows the cache to be corrupted: `get_node_by_hash` returns the wrong leaf's data, and the tree ends up with two leaves sharing the same hash, invalidating the Merkle root.

### Finding Description
`insert` guards against duplicate hashes at lines 372–374. `upsert`'s update path (when the key already exists) has no equivalent guard. The sequence is:

1. `remove_leaf(&leaf_A)` — removes `hash_A → index_A` from cache, marks `index_A` free.
2. `insert_entry_to_blob(index_A, block_with_hash_B)` → `add_leaf(index_A, leaf{hash_B})` — calls `leaf_hash_to_index.insert(hash_B, index_A)`, silently overwriting the existing `hash_B → index_B` entry.

After this, `leaf_hash_to_index[hash_B] = index_A` (wrong), `hash_A` is gone, and the blob has two leaves with `hash_B`.

### Impact Explanation
- `get_node_by_hash(hash_B)` returns `(key_A, value_A_new)` instead of `(key_B, value_B)` — forged DataLayer state lookup.
- The Merkle tree root is computed over a blob where two leaves share `hash_B`, producing a root hash that does not correspond to any valid tree state.
- Any downstream consumer relying on `get_node_by_hash` (e.g., delta/proof verification) receives incorrect data without any error.

### Likelihood Explanation
Any caller that can supply an arbitrary `new_hash` to `upsert` — including via the Python binding `py_upsert` or the DataLayer protocol path — can trigger this. No privilege beyond write access to the `MerkleBlob` is required. The Python binding is exposed directly: [9](#0-8) [10](#0-9) 

### Recommendation
Add a duplicate-hash guard at the start of `upsert`'s update path, mirroring `insert`:

```rust
pub fn upsert(&mut self, key: KeyId, value: ValueId, new_hash: &Hash) -> Result<(), Error> {
    let Ok((leaf_index, mut leaf, mut block)) = self.get_leaf_by_key(key) else {
        self.insert(key, value, new_hash, InsertLocation::Auto {})?;
        return Ok(());
    };

    // ADD: reject if new_hash already belongs to a different leaf
    if leaf.hash != *new_hash && self.block_status_cache.contains_leaf_hash(new_hash) {
        return Err(Error::HashAlreadyPresent());
    }

    self.block_status_cache.remove_leaf(&leaf)?;
    ...
```

### Proof of Concept

```rust
let mut blob = MerkleBlob::new(vec![]).unwrap();
let hash_a = Hash(Bytes32::new([1u8; 32]));
let hash_b = Hash(Bytes32::new([2u8; 32]));

blob.insert(KeyId(1), ValueId(10), &hash_a, InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(20), &hash_b, InsertLocation::Auto {}).unwrap();

// Upsert key_A with hash_B (already owned by key_B)
blob.upsert(KeyId(1), ValueId(99), &hash_b).unwrap(); // succeeds — no guard

// Cache is now corrupted: hash_b -> index of key_A
let (key, value) = blob.get_node_by_hash(hash_b).unwrap();
assert_eq!(key, KeyId(1)); // returns key_A, not key_B
// key_B's data is now unreachable by hash
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L199-208)
```rust
    fn remove_leaf(&mut self, node: &LeafNode) -> Result<(), Error> {
        let Some(index) = self.key_to_index.remove(&node.key) else {
            return Err(Error::UnknownKey(node.key));
        };
        self.leaf_hash_to_index.remove(&node.hash);

        self.free_indexes.insert(index);

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L326-332)
```rust
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L868-874)
```rust
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1198-1208)
```rust
    pub fn get_node_by_hash(&self, node_hash: Hash) -> Result<(KeyId, ValueId), Error> {
        let Some(index) = self.block_status_cache.get_index_by_leaf_hash(&node_hash) else {
            return Err(Error::LeafHashNotFound(node_hash));
        };

        let node = self
            .get_node(*index)?
            .expect_leaf("should only have leaves in the leaf hash to index cache");

        Ok((node.key, node.value))
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1435-1440)
```rust
    #[pyo3(name = "upsert")]
    pub fn py_upsert(&mut self, key: KeyId, value: ValueId, new_hash: Hash) -> PyResult<()> {
        self.upsert(key, value, &new_hash)?;

        Ok(())
    }
```

**File:** wheel/python/chia_rs/datalayer.pyi (L323-323)
```text
    def upsert(self, key: KeyId, value: ValueId, new_hash: bytes32) -> None: ...
```
