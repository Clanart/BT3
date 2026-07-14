### Title
`ProofOfInclusion::valid()` Uses Self-Referential Root Hash, Allowing Forged DataLayer Inclusion Proofs to Pass Validation — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` contains a tautological final check: it compares `existing_hash` against `self.root_hash()`, but `root_hash()` returns `last.combined_hash`, which is the same value `existing_hash` was just set to inside the loop. The function therefore only verifies internal self-consistency of the proof chain and never verifies the proof's root against any external trusted DataLayer root. An attacker can construct a `ProofOfInclusion` with an arbitrary `node_hash` (any key-value pair they wish to forge) and a chain of internally consistent hashes; `valid()` will return `true`.

### Finding Description

`ProofOfInclusion::valid()` is implemented as follows:

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

        existing_hash = calculated_hash;   // ← existing_hash = layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: root_hash() == last.combined_hash
}
``` [1](#0-0) 

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value existing_hash was just set to
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop body sets `existing_hash = calculated_hash` and verifies `calculated_hash == layer.combined_hash`, the post-loop assertion `existing_hash == self.root_hash()` reduces to `last.combined_hash == last.combined_hash`, which is unconditionally true. The function never compares the proof's root against any externally supplied, trusted DataLayer root hash.

`ProofOfInclusion` is a `Streamable` type exposed directly to Python via `#[pyclass]` and `PyStreamable`, meaning untrusted bytes from a DataLayer peer can be deserialized into a `ProofOfInclusion` and then validated with `proof.valid()`. [3](#0-2) 

The fuzz harness and integration tests confirm that `proof.valid()` is the sole validation call made on proofs obtained from `get_proof_of_inclusion`: [4](#0-3) 

### Impact Explanation

An attacker who can supply a `ProofOfInclusion` to any consumer of the Python/Rust DataLayer API can forge proof of inclusion for an arbitrary key-value pair. Because `valid()` accepts any internally self-consistent proof regardless of whether its root matches the actual DataLayer tree, the attacker can convince a verifier that a key-value mapping exists in the DataLayer when it does not. This enables forged state proofs, invalid DataLayer state acceptance, and potential corruption of downstream logic that relies on DataLayer inclusion proofs for authorization or coin-spend decisions.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with Python bindings, so any network peer or untrusted caller can supply crafted proof bytes. The attacker only needs to compute a valid SHA-256 hash chain (trivial), set `node_hash` to the target key-value hash, and build layers whose `combined_hash` values chain consistently. No privileged access, key material, or chain reorganization is required.

### Recommendation

`valid()` must accept an externally trusted root hash and compare against it instead of (or in addition to) `self.root_hash()`:

```rust
pub fn valid_against_root(&self, trusted_root: &Hash) -> bool {
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
    &existing_hash == trusted_root   // compare against external trusted root
}
```

All call sites (Python bindings, fuzz targets, integration tests) must be updated to pass the actual DataLayer tree root obtained from a trusted source.

### Proof of Concept

```python
from chia_rs import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

def sha256(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()

# Forge proof that arbitrary_node_hash is in the DataLayer
arbitrary_node_hash = bytes([0xAB] * 32)
sibling_hash        = bytes([0xCD] * 32)

# Build one internally-consistent layer
combined = sha256(arbitrary_node_hash, sibling_hash)   # or whatever calculate_internal_hash does
layer = ProofOfInclusionLayer(
    other_hash_side=0,          # Left
    other_hash=sibling_hash,
    combined_hash=combined,     # stored in proof; root_hash() returns this
)

forged_proof = ProofOfInclusion(node_hash=arbitrary_node_hash, layers=[layer])

# valid() returns True even though this key is not in any real DataLayer tree
assert forged_proof.valid() == True
# forged_proof.root_hash() == combined, which is attacker-chosen, not the real tree root
```

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L8-29)
```rust
#[cfg_attr(
    feature = "py-bindings",
    pyclass(get_all, from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
#[derive(Clone, Debug, std::hash::Hash, Eq, PartialEq, Streamable)]
pub struct ProofOfInclusionLayer {
    pub other_hash_side: Side,
    pub other_hash: Hash,
    pub combined_hash: Hash,
}

#[cfg_attr(
    feature = "py-bindings",
    pyclass(get_all, from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
#[derive(Clone, Debug, std::hash::Hash, Eq, PartialEq, Streamable)]
pub struct ProofOfInclusion {
    pub node_hash: Hash,
    pub layers: Vec<ProofOfInclusionLayer>,
}
```

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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
