### Title
`ProofOfInclusion::valid()` Never Compares Against an External Trusted Root — Forged Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only verifies the internal self-consistency of the proof chain. It never compares the computed root against any externally-supplied, trusted tree root. Because `root_hash()` is derived entirely from the proof's own fields, the final equality check inside `valid()` is a tautology. An attacker can construct a completely fabricated `ProofOfInclusion` for any arbitrary key-value pair, and `valid()` will return `true`.

### Finding Description

`ProofOfInclusion::valid()` is the sole public API for verifying DataLayer Merkle inclusion proofs. Its implementation is:

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

    existing_hash == self.root_hash()   // ← always true when loop completes
}
``` [1](#0-0) 

The helper `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken directly from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

The loop invariant guarantees that after the loop exits normally, `existing_hash` equals `last_layer.combined_hash`. `root_hash()` also returns `last_layer.combined_hash`. Therefore the final comparison `existing_hash == self.root_hash()` is **always true** when the loop completes without an early return. The function only verifies that the proof's own fields are mutually consistent — it never anchors the proof to any external, committed tree root.

The missing step — analogous to the missing `redeem()` call in the AaveStrategy report — is a comparison of the computed root against a caller-supplied, externally-trusted root hash. Without this step, the proof validation is incomplete and meaningless as a security check.

`ProofOfInclusion` is a `Streamable` type exposed directly to Python via `pymethods`: [3](#0-2) 

It is also part of the public Rust API returned by `MerkleBlob::get_proof_of_inclusion()`: [4](#0-3) 

And exposed via the Python wheel stub: [5](#0-4) 

Any DataLayer consumer — in Python or Rust — that calls `proof.valid()` to decide whether to trust a claimed key-value inclusion is completely unprotected against a forged proof.

### Impact Explanation

**High — DataLayer Merkle proof logic accepts forged inclusion.**

An attacker who can supply a `ProofOfInclusion` object (e.g., via the Python API, via deserialization from `from_bytes`, or via the `from_py_object` pyo3 derive) can prove inclusion of any arbitrary key-value pair in any fabricated tree root. Because `valid()` returns `true` for any internally-consistent proof regardless of what the actual committed root is, any DataLayer state verification that relies on `proof.valid()` alone can be bypassed. This allows untrusted input to prove invalid state, matching the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

The `valid()` method is the only verification primitive exposed for `ProofOfInclusion`. Its name strongly implies it is sufficient for proof verification. Any DataLayer consumer — including the Chia reference implementation in Python — that calls `proof.valid()` without separately comparing `proof.root_hash()` against a known-good committed root is vulnerable. The fuzz target for proofs of inclusion also only calls `proof.valid()` without an external root check: [6](#0-5) 

The Python test suite similarly calls only `proof_of_inclusion.valid()`: [7](#0-6) 

The attacker entry path is direct: deserialize or construct a `ProofOfInclusion` with arbitrary `node_hash` and a single self-consistent layer, then call `valid()`.

### Recommendation

`valid()` must accept an external trusted root hash and compare against it:

```rust
pub fn valid_against_root(&self, trusted_root: &Hash) -> bool {
    // ... existing chain check ...
    existing_hash == *trusted_root   // compare against external root, not self.root_hash()
}
```

The current `valid()` method should either be removed or clearly renamed to `is_internally_consistent()` with documentation warning that it does not verify against any committed root. All call sites — including the Python bindings, fuzz targets, and tests — must be updated to supply and compare against the actual committed tree root.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side, ProofOfInclusion, ProofOfInclusionLayer};

// Fabricate a proof for an arbitrary node_hash
let fake_node_hash: Hash = [0xAA; 32];
let fake_other_hash: Hash = [0xBB; 32];

// Compute what combined_hash must be for internal consistency
let combined = calculate_internal_hash(&fake_node_hash, Side::Left, &fake_other_hash);

let forged_proof = ProofOfInclusion {
    node_hash: fake_node_hash,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Left,
        other_hash: fake_other_hash,
        combined_hash: combined,  // internally consistent
    }],
};

// valid() returns true even though this proof was never generated from any real tree
assert!(forged_proof.valid());
// forged_proof.root_hash() == combined, which is attacker-controlled
``` [1](#0-0)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L32-38)
```rust
    pub fn root_hash(&self) -> Hash {
        if let Some(last) = self.layers.last() {
            last.combined_hash
        } else {
            self.node_hash
        }
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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L61-72)
```rust
#[cfg(feature = "py-bindings")]
#[pymethods]
impl ProofOfInclusion {
    #[pyo3(name = "root_hash")]
    pub fn py_root_hash(&self) -> Hash {
        self.root_hash()
    }
    #[pyo3(name = "valid")]
    pub fn py_valid(&self) -> bool {
        self.valid()
    }
}
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1196)
```rust
    pub fn get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
        let mut index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;

        let node = self
            .get_node(index)?
            .expect_leaf("key to index mapping should only have leaves");

        let parents = self.get_lineage_blocks_with_indexes(index)?;
        let mut layers: Vec<proof_of_inclusion::ProofOfInclusionLayer> = Vec::new();
        let mut parents_iter = parents.iter();
        // first in the lineage is the index itself, second is the first parent
        parents_iter.next();
        for (next_index, block) in parents_iter {
            if block.metadata.dirty {
                return Err(Error::Dirty(*next_index));
            }
            let parent = block
                .node
                .expect_internal("all nodes after the first should be internal");
            let sibling_index = parent.sibling_index(index)?;
            let sibling_block = self.get_block(sibling_index)?;
            let sibling = sibling_block.node;
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
            layers.push(layer);
            index = *next_index;
        }

        Ok(proof_of_inclusion::ProofOfInclusion {
            node_hash: node.hash,
            layers,
        })
    }
```

**File:** wheel/python/chia_rs/datalayer.pyi (L236-243)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```

**File:** tests/test_datalayer.py (L337-339)
```python
        for kv_id in keys_values.keys():
            proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
            assert proof_of_inclusion.valid()
```
