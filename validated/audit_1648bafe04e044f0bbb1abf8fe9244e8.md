### Title
`batch_insert` Bypasses Duplicate-Key Validation, Allowing Sequential `KeyId` to Map to Multiple Content Hashes and Corrupt the DataLayer Merkle Root — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

### Summary

`KeyId` and `ValueId` in the DataLayer Merkle tree are sequential SQLite row IDs (opaque integers), not derived from the actual key/value content bytes. The single-item `insert()` path enforces uniqueness of `KeyId` and leaf `Hash`. However, `batch_insert()` bypasses both checks for all items beyond the first two (or for all items when the tree already has ≥ 2 leaves), allowing the same `KeyId` to be written into the blob multiple times with different content hashes. This corrupts the `BlockStatusCache` (the in-memory index) and produces a Merkle root that is inconsistent with the cache, enabling forged or unprovable inclusion states.

---

### Finding Description

**Root cause — `KeyId` is a sequential, non-content-derived identifier**

`KeyId` and `ValueId` are explicitly documented as SQLite row IDs:

> "Key and value ids are provided from outside of this code and are implemented as the row id from sqlite which is a signed 8 byte integer. The actual key and value data bytes will not be handled within this code, only outside." [1](#0-0) 

Because `KeyId` is a sequential integer and not a hash of the actual key bytes, the same integer can legitimately appear in two different leaf nodes with different `Hash` values — the Merkle tree has no structural mechanism to prevent this other than the explicit duplicate check in `insert()`.

**Root cause — `batch_insert()` skips the duplicate check**

`insert()` enforces two invariants before writing:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
``` [2](#0-1) 

`batch_insert()` calls `self.insert()` for at most the last two items in the input vector (only when the tree has ≤ 1 existing leaves). All remaining items — and **all** items when the tree already has ≥ 2 leaves — are written directly via `insert_entry_to_blob()` with no duplicate check:

```rust
for ((key, value), hash) in keys_values_hashes {
    let new_leaf_index = self.get_new_index();
    let new_block = Block { ..., node: Node::Leaf(LeafNode { hash, key, value, ... }) };
    self.insert_entry_to_blob(new_leaf_index, &new_block)?;
    indexes.push(new_leaf_index);
}
``` [3](#0-2) 

**Corruption mechanism**

`insert_entry_to_blob()` updates `BlockStatusCache` (including `key_to_index` and `leaf_hash_to_index`). When a duplicate `KeyId` is written:

1. A second leaf node with the same `KeyId` but a different `Hash` is appended to the blob.
2. `key_to_index.insert(key, new_index)` silently overwrites the previous mapping — the first leaf is now orphaned in the blob but invisible to the cache.
3. The Merkle root is computed over **both** leaves (the orphaned one and the new one), but `get_proof_of_inclusion(key)` only finds the second leaf.
4. Any proof generated is invalid against the actual root; any root comparison against an expected value will disagree.

The `check_integrity()` function would detect this (`leaf_count != key_to_index_cache_length`), but it is not called automatically after `batch_insert()`. [4](#0-3) 

The Python binding `py_batch_insert` exposes this path directly:

```rust
pub fn py_batch_insert(&mut self, keys_values: Vec<(KeyId, ValueId)>, hashes: Vec<Hash>) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
``` [5](#0-4) 

---

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

A corrupted `MerkleBlob` produces a root hash that does not correspond to any consistent set of key-value pairs. Downstream consumers that verify inclusion proofs against this root will either:
- Accept proofs for keys that are not actually present (the orphaned leaf's hash is in the root but not in the cache), or
- Reject valid proofs (the cache-tracked leaf's proof does not reconstruct the actual root).

Both outcomes represent committed state corruption in the DataLayer Merkle tree.

---

### Likelihood Explanation

`batch_insert` is the primary bulk-load path used during DataLayer sync. The Python binding is directly callable. Any caller — including DataLayer node software processing data received from a peer — that provides a batch containing a repeated `KeyId` (e.g., due to a re-sync, a bug in the upstream data source, or a malicious peer) will silently corrupt the tree. The existing test suite (`test_batch_insert`) only tests non-overlapping key ranges and does not exercise the duplicate-key case through `batch_insert`. [6](#0-5) 

---

### Recommendation

Add the same duplicate-key and duplicate-hash checks to the fast path inside `batch_insert()` that `insert()` already enforces:

```rust
for ((key, value), hash) in keys_values_hashes {
    if self.block_status_cache.contains_key(key) {
        return Err(Error::KeyAlreadyPresent());
    }
    if self.block_status_cache.contains_leaf_hash(&hash) {
        return Err(Error::HashAlreadyPresent());
    }
    // ... existing insertion logic
}
```

Alternatively, pre-validate the entire input batch for duplicates (both within the batch and against the existing tree) before any writes begin, so the tree is never left in a partially-written corrupt state.

---

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
import hashlib

def h(n: int) -> bytes:
    return hashlib.sha256(n.to_bytes(8, "big")).digest()

# Pre-populate so leaf_count > 1 (bypasses the self.insert() branch entirely)
blob = MerkleBlob(bytearray())
blob.insert(KeyId(100), ValueId(100), h(100))
blob.insert(KeyId(200), ValueId(200), h(200))

# batch_insert with a duplicate KeyId=100 but a different hash
# Neither item goes through self.insert(), so no duplicate check fires
blob.batch_insert(
    [(KeyId(100), ValueId(999)), (KeyId(300), ValueId(300))],
    [h(999), h(300)]
)

blob.calculate_lazy_hashes()

# The cache only knows about one KeyId(100) leaf, but the blob has two.
# check_integrity() will now raise because leaf_count != cache length.
blob.check_integrity()  # raises IntegrityKeyToIndexCacheLengthError
``` [7](#0-6) [2](#0-1)

### Citations

**File:** crates/chia-datalayer/src/merkle/format.rs (L62-76)
```rust
/// Key and value ids are provided from outside of this code and are implemented as
/// the row id from sqlite which is a signed 8 byte integer.  The actual key and
/// value data bytes will not be handled within this code, only outside.
#[cfg_attr(
    feature = "py-bindings",
    pyclass(from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
#[cfg_attr(feature = "arbitrary", derive(arbitrary::Arbitrary))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Streamable)]
// ISSUE: this cfg()/cfg(not()) is terrible, but there's an issue with pyo3
//        being found with a cfg_attr
//        https://github.com/PyO3/pyo3/issues/5125
#[cfg(feature = "py-bindings")]
pub struct KeyId(#[pyo3(get, name = "raw")] pub i64);
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
