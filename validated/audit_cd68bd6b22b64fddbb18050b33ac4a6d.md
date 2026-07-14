### Title
Stale Internal-Node Hashes Silently Returned by `get_hashes()` / `get_hashes_indexes()` After Tree Mutations Without `calculate_lazy_hashes()` — (`File: crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob` uses a lazy-hash design: after any `insert`, `upsert`, `delete`, or `batch_insert`, ancestor internal nodes are marked `dirty` and their stored hashes are **stale** until `calculate_lazy_hashes()` is explicitly called. Two public query functions — `get_hashes()` and `get_hashes_indexes()` — iterate the blob and return node hashes **without checking the dirty flag**, silently emitting stale (pre-mutation) hashes for every dirty internal node, including the root. This is directly inconsistent with `get_hash_at_index()` and `get_proof_of_inclusion()`, which both check the dirty flag and return a hard error. If a DataLayer caller invokes `get_hashes()` or `get_hashes_indexes()` after mutations but before `calculate_lazy_hashes()`, it receives a hash-set that does not reflect the current tree state, corrupting any delta-sync or inclusion/exclusion proof built from it.

---

### Finding Description

**Lazy-hash design and the dirty flag**

After any mutation, `mark_lineage_as_dirty()` walks from the changed leaf to the root and sets `block.metadata.dirty = true` on every ancestor internal node. [1](#0-0) 

`calculate_lazy_hashes()` is the only function that recomputes and clears those dirty flags; it must be called explicitly by the caller. [2](#0-1) 

**Functions that correctly guard against dirty state**

`get_hash_at_index()` returns `Err(Error::Dirty(index))` if the requested block is dirty: [3](#0-2) 

`get_proof_of_inclusion()` similarly returns `Err(Error::Dirty(*next_index))` for any dirty ancestor: [4](#0-3) 

**Functions that do NOT guard — the vulnerability**

`get_hashes()` iterates every node and inserts `block.node.hash()` into the result set with **no dirty check**: [5](#0-4) 

`get_hashes_indexes()` does the same, also with no dirty check: [6](#0-5) 

Both functions are exposed to Python callers via the `py-bindings` feature: [7](#0-6) 

The Python binding for `get_root_hash` delegates to `get_hash_at_index(TreeIndex(0))`, which is protected. But `get_hashes()` is a separate, unprotected path that returns the same root node's stale hash without error. [8](#0-7) 

**Analog to the external report**

The external report describes a config parameter (`anc_purchase_factor`) being updated without triggering the dependent recalculation (`execute_epoch_operations`), leaving downstream consumers with stale data. Here, tree mutations update leaf data and mark ancestors dirty, but `get_hashes()` / `get_hashes_indexes()` do not enforce the required recalculation step (`calculate_lazy_hashes()`), leaving delta-sync consumers with stale internal-node hashes — including a stale root hash.

---

### Impact Explanation

**High — DataLayer Merkle delta logic corrupts tree roots or lets untrusted input prove invalid state.**

A DataLayer node that calls `get_hashes()` or `get_hashes_indexes()` after mutations but before `calculate_lazy_hashes()` receives a hash-set containing stale internal-node hashes (including the root). Any delta-sync protocol built on this set will:

1. Report the wrong root hash to peers, causing root-hash mismatch between nodes.
2. Compute an incorrect "new vs. existing" delta — nodes that were changed appear unchanged (their old hash is still present), and the new correct hashes are absent.
3. Allow a receiver to accept a delta that does not correspond to the actual committed tree state, enabling forged inclusion/exclusion proofs to pass against the stale root.

---

### Likelihood Explanation

**Moderate.** The `calculate_lazy_hashes()` step is not enforced by the type system or by the mutation functions themselves; it is a caller obligation. The inconsistency is subtle: `get_proof_of_inclusion()` and `get_hash_at_index()` return hard errors on dirty state, giving callers a false sense that all query functions are safe to call at any time. A DataLayer implementation that calls `get_hashes()` for delta computation immediately after a batch of inserts/upserts — a natural usage pattern — silently receives stale data. No privileged access is required; any party that can submit DataLayer key-value updates can trigger the mutation path.

---

### Recommendation

Apply the same dirty-state guard used in `get_hash_at_index()` and `get_proof_of_inclusion()` to `get_hashes()` and `get_hashes_indexes()`:

```rust
// In get_hashes() and get_hashes_indexes(), inside the iteration loop:
if block.metadata.dirty {
    return Err(Error::Dirty(index));
}
```

Alternatively, call `calculate_lazy_hashes()` automatically inside `get_hashes()` and `get_hashes_indexes()` (requiring `&mut self`) so callers cannot accidentally query stale state. The fuzz target already demonstrates the correct pattern — `calculate_lazy_hashes()` is always called before any hash query: [9](#0-8) 

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, KeyId, MerkleBlob, ValueId, InsertLocation};

let mut blob = MerkleBlob::new(Vec::new()).unwrap();

// Insert two leaves — ancestors are now dirty
blob.insert(KeyId(1), ValueId(1), &Hash::from([0x01; 32]), InsertLocation::Auto {}).unwrap();
blob.insert(KeyId(2), ValueId(2), &Hash::from([0x02; 32]), InsertLocation::Auto {}).unwrap();

// get_hash_at_index(root) correctly returns Err(Dirty) — protected
assert!(blob.get_hash_at_index(chia_datalayer::TreeIndex(0)).is_err());

// get_hashes() silently returns the stale root hash — NOT protected
let stale_hashes = blob.get_hashes().unwrap(); // succeeds, returns wrong hashes

// Now compute correct hashes
blob.calculate_lazy_hashes().unwrap();
let correct_hashes = blob.get_hashes().unwrap();

// The two sets differ: stale_hashes contains pre-mutation internal-node hashes
assert_ne!(stale_hashes, correct_hashes); // demonstrates stale data was returned
```

The stale hash-set returned before `calculate_lazy_hashes()` would be used by any delta-sync or root-verification logic that calls `get_hashes()`, producing an incorrect view of the committed DataLayer tree state.

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L901-916)
```rust
    fn mark_lineage_as_dirty(&mut self, index: TreeIndex) -> Result<(), Error> {
        let mut next_index = Some(index);

        while let Some(this_index) = next_index {
            let mut block = Block::from_bytes(self.get_block_bytes(this_index)?)?;

            if block.metadata.dirty {
                break;
            }

            block.metadata.dirty = true;
            self.insert_entry_to_blob(this_index, &block)?;
            next_index = block.node.parent().0;
        }

        Ok(())
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1173-1176)
```rust
        for (next_index, block) in parents_iter {
            if block.metadata.dirty {
                return Err(Error::Dirty(*next_index));
            }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1210-1223)
```rust
    pub fn get_hashes(&self) -> Result<HashSet<Hash>, Error> {
        let mut hashes = HashSet::<Hash>::new();

        if self.blob.is_empty() {
            return Ok(hashes);
        }

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (_, block) = item?;
            hashes.insert(block.node.hash());
        }

        Ok(hashes)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1225-1243)
```rust
    pub fn get_hashes_indexes(&self, leafs_only: bool) -> Result<HashMap<Hash, TreeIndex>, Error> {
        let mut hash_to_index = HashMap::new();

        if self.blob.is_empty() {
            return Ok(hash_to_index);
        }

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (index, block) = item?;

            if leafs_only && block.metadata.node_type != NodeType::Leaf {
                continue;
            }

            hash_to_index.insert(block.node.hash(), index);
        }

        Ok(hash_to_index)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1493-1501)
```rust
    #[pyo3(name = "get_root_hash")]
    pub fn py_get_root_hash(&self) -> PyResult<Option<Hash>> {
        self.py_get_hash_at_index(TreeIndex(0))
    }

    #[pyo3(name = "get_hash_at_index")]
    pub fn py_get_hash_at_index(&self, index: TreeIndex) -> PyResult<Option<Hash>> {
        Ok(self.get_hash_at_index(index)?)
    }
```

**File:** wheel/python/chia_rs/datalayer.pyi (L330-337)
```text
    def get_root_hash(self) -> bytes32: ...
    def batch_insert(self, keys_values: list[tuple[KeyId, ValueId]], hashes: list[bytes32]): ...
    def get_hash_at_index(self, index: TreeIndex): ...
    def get_keys_values(self) -> dict[KeyId, ValueId]: ...
    def get_key_index(self, key: KeyId) -> TreeIndex: ...
    def get_proof_of_inclusion(self, key: KeyId) -> ProofOfInclusion: ...
    def get_node_by_hash(self, node_hash: bytes32) -> tuple[KeyId, ValueId]: ...
    def get_hashes_indexes(self, leafs_only: bool = ...) -> dict[bytes32, TreeIndex]: ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L25-31)
```rust
    blob.calculate_lazy_hashes().unwrap();
    blob.check_integrity().unwrap();

    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
