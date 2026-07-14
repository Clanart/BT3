### Title
`MerkleBlob::batch_insert` Silently Accepts Duplicate Keys, Corrupting Tree Root and Enabling Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::batch_insert` bypasses the duplicate-key and duplicate-hash guards that the single-item `insert` method enforces. When a batch contains a key already present in the tree — or a duplicate key within the batch itself — `batch_insert` silently writes a second leaf node with the same key into the blob. Because `insert_entry_to_blob` calls `block_status_cache.add_leaf`, which uses `HashMap::insert` (silent overwrite), the cache's `key_to_index` mapping is silently redirected to the new leaf while the old leaf remains physically present in the tree and continues to contribute to the Merkle root hash. The result is a tree whose root hash is inconsistent with what the cache believes, enabling forged proofs of inclusion.

---

### Finding Description

**`insert` enforces uniqueness; `batch_insert` does not.**

`MerkleBlob::insert` (lines 362–374) guards against duplicates before touching the blob:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(hash) {
    return Err(Error::HashAlreadyPresent());
}
```

`batch_insert` (lines 570–657) has two code paths:

1. **When `leaf_count() <= 1`** (lines 578–585): it `pop()`s the **last two** items from the vector and routes them through `insert()` (with duplicate checks). The **remaining** items (all earlier indices) are then processed in the fast path below — **without any duplicate check**.

2. **When `leaf_count() > 1`** (lines 587–603): **every** item in the batch is written directly via `insert_entry_to_blob` — **no duplicate check at all**.

`insert_entry_to_blob` (lines 1013–1030) calls `block_status_cache.add_leaf`:

```rust
match block.node {
    Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
    ...
}
```

`add_leaf` (lines 188–193) uses `HashMap::insert`, which **silently overwrites** any existing entry:

```rust
fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
    self.free_indexes.shift_remove(&index);
    self.key_to_index.insert(leaf.key, index);       // silent overwrite
    self.leaf_hash_to_index.insert(leaf.hash, index); // silent overwrite
}
```

**Resulting corruption:**

After `batch_insert` completes with a duplicate key `K`:
- Two physical leaf nodes for `K` exist in the blob — the original leaf (still connected to its parent in the tree) and the new leaf (connected via the newly inserted subtree).
- The root hash is computed over **both** leaves, so it reflects a tree with two entries for `K`.
- The cache's `key_to_index[K]` points only to the **new** leaf (the old entry was silently overwritten).
- `get_proof_of_inclusion(K)` traverses the lineage of the new leaf and returns a proof whose `root_hash()` equals the actual (corrupted) root — so `proof.valid()` returns `true`.
- But the proof attests to the **new** value for `K`, not the original value. The original leaf is unreachable via the cache but still embedded in the tree structure.
- Any consumer that checks `proof.root_hash() == expected_root` will accept the forged proof, because the corrupted root is the one that was committed.

---

### Impact Explanation

The DataLayer Merkle tree root is corrupted: it encodes two leaves for the same key. `get_proof_of_inclusion` returns a structurally valid proof (passes `proof.valid()`) for the attacker-chosen value, while the original value's leaf is silently orphaned in the cache but still present in the blob. This allows:

- **Forged inclusion proofs**: a caller can prove that key `K` maps to value `V_new` when the committed state should show `V_old`.
- **Corrupted tree roots**: the root hash committed on-chain diverges from the canonical single-key-per-entry invariant, making the DataLayer store's state permanently inconsistent.
- **Integrity check bypass**: `check_integrity` (lines 821–887) validates that every leaf in the cache has a matching blob entry, but it does **not** detect the inverse — blob leaves that are no longer in the cache. The orphaned original leaf passes undetected.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

`batch_insert` is exposed directly to Python via `py_batch_insert` (lines 1503–1519) and is the primary bulk-insertion API used by the DataLayer node software. The Python binding performs no deduplication:

```rust
pub fn py_batch_insert(
    &mut self,
    keys_values: Vec<(KeyId, ValueId)>,
    hashes: Vec<Hash>,
) -> PyResult<()> {
    ...
    self.batch_insert(zip(keys_values, hashes).collect())?;
    Ok(())
}
```

Any DataLayer store owner who submits a batch containing a key already present in their store (or a repeated key within a single batch) will silently trigger this corruption. Because the DataLayer protocol allows store owners to submit arbitrary key-value updates, this is reachable by an unprivileged actor with no special privileges beyond owning a DataLayer store.

---

### Recommendation

Add the same duplicate-key and duplicate-hash guards to the fast path of `batch_insert` that `insert` already enforces. Before calling `insert_entry_to_blob` for each item in the batch loop, check:

```rust
if self.block_status_cache.contains_key(key) {
    return Err(Error::KeyAlreadyPresent());
}
if self.block_status_cache.contains_leaf_hash(&hash) {
    return Err(Error::HashAlreadyPresent());
}
```

Alternatively, deduplicate the input vector before processing and return an error if duplicates are detected, consistent with the contract of `insert`.

---

### Proof of Concept

```rust
use chia_datalayer::{Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use chia_protocol::Bytes32;

fn sha256_of(n: u8) -> Hash {
    use chia_sha2::Sha256;
    let mut h = Sha256::new();
    h.update(&[n]);
    Hash(Bytes32::new(h.finalize()))
}

fn main() {
    // Start with a tree that already has 2 leaves (leaf_count > 1),
    // so batch_insert takes the fully-unchecked fast path.
    let mut blob = MerkleBlob::new(vec![]).unwrap();
    blob.insert(KeyId(1), ValueId(10), &sha256_of(1), InsertLocation::Auto {}).unwrap();
    blob.insert(KeyId(2), ValueId(20), &sha256_of(2), InsertLocation::Auto {}).unwrap();
    blob.calculate_lazy_hashes().unwrap();

    let root_before = blob.get_root_hash().unwrap();

    // batch_insert with KeyId(1) already present — no error is returned.
    blob.batch_insert(vec![
        ((KeyId(1), ValueId(99)), sha256_of(99)), // duplicate key, new value
    ]).unwrap();
    blob.calculate_lazy_hashes().unwrap();

    let root_after = blob.get_root_hash().unwrap();

    // Root has changed — two leaves for KeyId(1) are now in the tree.
    assert_ne!(root_before, root_after, "root should be corrupted");

    // Proof is structurally valid but attests to ValueId(99), not ValueId(10).
    let proof = blob.get_proof_of_inclusion(KeyId(1)).unwrap();
    assert!(proof.valid(), "forged proof passes valid()");
    assert_eq!(proof.root_hash(), root_after, "proof root matches corrupted root");

    println!("Forged proof accepted. Cache now maps KeyId(1) -> ValueId(99).");
    println!("Original ValueId(10) leaf is still in the blob but unreachable via cache.");
}
```

**Key lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-603)
```rust
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
