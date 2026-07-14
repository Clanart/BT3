### Title
`ProofOfInclusion::valid()` Does Not Validate Against a Trusted Root Hash — Forged Inclusion Proofs Pass Validation - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks the internal self-consistency of a proof's hash chain. It derives its "root" from the proof's own last `combined_hash` field rather than accepting an external trusted root as a parameter. Any attacker who can supply a `ProofOfInclusion` object (via the `Streamable`/Python deserialization boundary) can construct a fully fabricated proof for an arbitrary `node_hash` that passes `valid()` without being anchored to any real DataLayer tree root.

### Finding Description

`ProofOfInclusion` is a `Streamable`-derived struct that can be deserialized from untrusted bytes and is exposed directly to Python via `py_valid()`. Its `valid()` method is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself, not from a trusted source
    } else {
        self.node_hash
    }
}

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
    existing_hash == self.root_hash()   // ← compares against self.root_hash(), not an external root
}
```

The final check `existing_hash == self.root_hash()` is a tautology: `self.root_hash()` returns `last.combined_hash`, which is the same value that `existing_hash` was just set to in the last loop iteration. The function therefore only verifies that the hash chain is internally self-consistent — it never checks that the computed root matches any externally trusted tree root.

An attacker can construct a valid-looking `ProofOfInclusion` for any arbitrary `node_hash` by:
1. Choosing any target leaf hash as `node_hash`.
2. Choosing any arbitrary `other_hash` values and computing each `combined_hash` correctly using `calculate_internal_hash`.
3. The resulting proof will pass `valid()` unconditionally.

The Python binding exposes this directly:

```rust
#[pyo3(name = "valid")]
pub fn py_valid(&self) -> bool {
    self.valid()
}
```

And the established usage pattern in both tests and the fuzz target is to call `proof.valid()` as the sole correctness check, with no subsequent comparison of `proof.root_hash()` against a trusted external root:

```rust
// fuzz target
assert!(proof.valid());

// test
assert!(proof_of_inclusion.valid());
```

```python
# Python test
proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
assert proof_of_inclusion.valid()
```

The struct derives `Streamable`, meaning it can be deserialized from attacker-controlled bytes at the Python/wasm boundary and then passed to `valid()`.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any consumer of `ProofOfInclusion` that calls `valid()` as its sole check — the pattern established by the library's own tests and fuzz targets — will accept a forged proof for any arbitrary leaf. This allows an untrusted party to prove that a key-value pair is included in a DataLayer tree when it is not, enabling false state attestation across any system that relies on DataLayer inclusion proofs for authorization or data integrity decisions.

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and exposed to Python. The `valid()` method is the only validation method provided; there is no `valid_for_root(trusted_root: Hash)` API. The misleading name `valid()` strongly implies complete validation. Every usage in the codebase — tests, fuzz targets, and Python bindings — calls `valid()` alone without a root comparison, establishing this as the intended and expected usage pattern. Any downstream chia-blockchain code following this pattern is vulnerable.

### Recommendation

`valid()` must accept a trusted external root hash as a required parameter and compare the computed root against it:

```rust
pub fn valid_for_root(&self, trusted_root: &Hash) -> bool {
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
    &existing_hash == trusted_root   // ← compare against caller-supplied trusted root
}
```

The parameterless `valid()` should be removed or deprecated to prevent misuse. The Python binding should expose only `valid_for_root(root: bytes)`. All call sites — including the fuzz target and tests — must be updated to supply the actual tree root obtained from a trusted source (e.g., `merkle_blob.get_root()`).

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

// Forge a proof for an arbitrary leaf that is NOT in any real tree.
let fake_leaf_hash: Hash = [0xAA; 32];
let fake_other_hash: Hash = [0xBB; 32];

// Compute combined_hash exactly as calculate_internal_hash would.
// (Side::Left means fake_leaf_hash || fake_other_hash)
let combined = chia_datalayer::calculate_internal_hash(
    &fake_leaf_hash,
    Side::Left,
    &fake_other_hash,
);

let forged_proof = ProofOfInclusion {
    node_hash: fake_leaf_hash,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Left,
        other_hash: fake_other_hash,
        combined_hash: combined,
    }],
};

// Passes valid() despite not corresponding to any real tree.
assert!(forged_proof.valid());
// forged_proof.root_hash() == combined, which is attacker-controlled.
```

The forged proof is also serializable via `Streamable` and can be sent across the Python boundary, where `proof.valid()` will return `True`.

---

**Root cause:** [1](#0-0) 

**`root_hash()` reads from the proof itself:** [2](#0-1) 

**`valid()` final comparison is a tautology:** [3](#0-2) 

**Python binding exposes the flawed `valid()` directly:** [4](#0-3) 

**Fuzz target uses `valid()` as sole check:** [5](#0-4) 

**`ProofOfInclusion` is `Streamable` (deserializable from untrusted bytes):** [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-28)
```rust
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
```

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L32-58)
```rust
    pub fn root_hash(&self) -> Hash {
        if let Some(last) = self.layers.last() {
            last.combined_hash
        } else {
            self.node_hash
        }
    }

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L68-71)
```rust
    #[pyo3(name = "valid")]
    pub fn py_valid(&self) -> bool {
        self.valid()
    }
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L29-31)
```rust
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
