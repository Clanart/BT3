### Title
MerkleBlob Python Binding Accepts Crafted Blobs with Forged Internal Hashes, Returning Attacker-Controlled Root Hash — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

The `MerkleBlob` Python constructor (`py_init`) accepts arbitrary bytes and constructs a `MerkleBlob` via `MerkleBlob::new` without verifying that stored internal node hashes are cryptographically correct. Because `get_hash_at_index` (and `get_root_hash`) only gate on the `dirty` metadata flag — which is stored in the blob itself and fully attacker-controlled — a crafted blob with `dirty = false` and zeroed (or arbitrary) internal hashes passes all checks and causes the Python API to return a forged DataLayer root hash. `check_integrity` does not detect the mismatch because `check_just_integrity` never compares stored hashes to recomputed values.

---

### Finding Description

**Entrypoint — `py_init` (line 1376):**

The Python `#[new]` constructor reads raw bytes from a `PyBuffer<u8>` and delegates directly to `MerkleBlob::new`: [1](#0-0) 

**`MerkleBlob::new` (line 316):**

The only validation is that `blob.len() % BLOCK_SIZE == 0` and that `BlockStatusCache::new` succeeds (which only builds structural indexes — free slots, key→index, leaf-hash→index). No hash correctness is checked. The blob is returned as-is: [2](#0-1) 

**`get_hash_at_index` (line 982):**

The only guard before returning the stored hash is the `dirty` flag. If `dirty = false` (which the attacker sets in the crafted blob), the stored hash — zero or any arbitrary value — is returned directly without recomputation: [3](#0-2) 

**`py_get_root_hash` (line 1493):**

The Python-exposed root hash method calls `get_hash_at_index(TreeIndex(0))` with no additional verification: [4](#0-3) 

**`check_integrity` / `check_just_integrity` do NOT catch hash mismatches:**

`check_integrity` clones the blob, calls `calculate_lazy_hashes` on the clone, then calls `check_just_integrity` on the clone. But `check_just_integrity` only verifies structural invariants (parent-child pointers, cache counts, index accounting) — it never compares the stored internal node hash to `internal_hash(left_hash, right_hash)`. The original blob's hashes are never validated: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

An attacker who can supply bytes to the `MerkleBlob` Python constructor (e.g., via DataLayer delta/sync, peer-provided blob data, or any deserialization path that feeds external bytes into `MerkleBlob(bytes)`) can cause the DataLayer node to treat an arbitrary value (including all-zeros) as the committed Merkle root. This directly enables forged DataLayer root acceptance: the Python caller receives a cryptographically invalid root hash from `get_root_hash()` with no error raised, violating the invariant that the exposed root hash is always the correct Merkle commitment over the stored key-value data.

This matches: **High — DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

---

### Likelihood Explanation

The `dirty` flag is stored in the blob bytes themselves (in `NodeMetadata`), so any attacker who can craft a structurally valid blob (correct `BLOCK_SIZE` multiples, valid parent-child pointers, valid node types) with `dirty = false` and zeroed internal hashes can trigger this. The Python binding is the primary DataLayer interface and is used in production DataLayer node software. If any DataLayer sync, delta, or peer-data path passes external bytes to `MerkleBlob(bytes)`, the path is directly reachable by an unprivileged network peer.

---

### Recommendation

In `MerkleBlob::new` (or in `py_init`), after loading the blob, recompute all internal node hashes bottom-up and compare them to the stored values, returning an error on mismatch. Alternatively, always call `calculate_lazy_hashes` after construction from external bytes and then verify the resulting hashes match what was stored. The `dirty` flag must not be trusted as a correctness signal when loading from untrusted external input.

---

### Proof of Concept

```python
import struct

# BLOCK_SIZE is known from the codebase (e.g., 116 bytes per block).
# Craft a minimal 3-block blob: root internal node (index 0), left leaf (index 1), right leaf (index 2).
# All internal node hash bytes = 0x00, dirty flag = 0 (not dirty).
# Structural fields (parent, left, right, key, value, node_type) set to valid values.

# After constructing the blob bytes with zeroed internal hash and dirty=false:
blob = craft_valid_structure_zeroed_internal_hash()  # attacker-controlled bytes

mb = MerkleBlob(blob)
root = mb.get_root_hash()
assert root == bytes(32)  # returns all-zeros, no error raised
# check_integrity() also passes — structural checks pass, hash values never compared
mb.check_integrity()  # does not raise
```

The call to `get_root_hash()` returns `bytes(32)` (all zeros) without raising `Error::Dirty` because the attacker set `dirty = false` in the crafted blob's metadata bytes. `check_integrity()` also passes because `check_just_integrity` never compares stored hashes to recomputed values.

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L316-332)
```rust
    pub fn new(blob: Vec<u8>) -> Result<Self, Error> {
        let length = blob.len();
        let remainder = length % BLOCK_SIZE;
        if remainder != 0 {
            return Err(Error::InvalidBlobLength(remainder));
        }

        let block_status_cache = BlockStatusCache::new(&blob)?;

        let self_ = Self {
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L812-819)
```rust
    pub fn check_integrity(&self) -> Result<(), Error> {
        self.check_just_integrity()?;

        let mut clone = self.clone();
        clone.check_integrity_on_drop = false;
        clone.calculate_lazy_hashes()?;
        clone.check_just_integrity()
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L821-887)
```rust
    fn check_just_integrity(&self) -> Result<(), Error> {
        let mut leaf_count: usize = 0;
        let mut internal_count: usize = 0;
        let mut child_to_parent: HashMap<TreeIndex, TreeIndex> = HashMap::new();

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (index, block) = item?;
            if let Some(parent) = block.node.parent().0 {
                if child_to_parent.remove(&index) != Some(parent) {
                    return Err(Error::IntegrityParentChildMismatch(index));
                }
            }
            match block.node {
                Node::Internal(node) => {
                    internal_count += 1;
                    child_to_parent.insert(node.left, index);
                    child_to_parent.insert(node.right, index);
                }
                Node::Leaf(node) => {
                    leaf_count += 1;
                    let cached_index = self
                        .block_status_cache
                        .get_index_by_key(node.key)
                        .ok_or(Error::IntegrityKeyNotInCache(node.key))?;
                    if *cached_index != index {
                        return Err(Error::IntegrityKeyToIndexCacheIndex(
                            node.key,
                            index,
                            *cached_index,
                        ));
                    }
                    assert!(
                        !self.block_status_cache.is_index_free(index),
                        "{}",
                        format!("active index found in free index list: {index:?}")
                    );
                }
            }
        }

        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
        let total_count = leaf_count + internal_count + self.block_status_cache.free_index_count();
        let extend_index = self.extend_index();
        if total_count != extend_index.0 as usize {
            return Err(Error::IntegrityTotalNodeCount(extend_index, total_count));
        }
        if !child_to_parent.is_empty() {
            return Err(Error::IntegrityUnmatchedChildParentRelationships(
                child_to_parent.len(),
            ));
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L982-993)
```rust
    pub fn get_hash_at_index(&self, index: TreeIndex) -> Result<Option<Hash>, Error> {
        if self.block_status_cache.no_keys() {
            return Ok(None);
        }

        let block = self.get_block(index)?;
        if block.metadata.dirty {
            return Err(Error::Dirty(index));
        }

        Ok(Some(block.node.hash()))
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1375-1386)
```rust
    #[new]
    pub fn py_init(blob: PyBuffer<u8>) -> PyResult<Self> {
        assert!(
            blob.is_c_contiguous(),
            "from_bytes() must be called with a contiguous buffer"
        );
        #[allow(unsafe_code)]
        let slice =
            unsafe { std::slice::from_raw_parts(blob.buf_ptr() as *const u8, blob.len_bytes()) };

        Ok(Self::new(Vec::from(slice))?)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1493-1496)
```rust
    #[pyo3(name = "get_root_hash")]
    pub fn py_get_root_hash(&self) -> PyResult<Option<Hash>> {
        self.py_get_hash_at_index(TreeIndex(0))
    }
```
