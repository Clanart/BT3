### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Trusted Root Hash, Accepting Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle proof module only checks the internal self-consistency of the proof structure. It does not verify the computed root against any externally trusted root hash. The final comparison in the function is a tautology — it always evaluates to `true` when the loop completes — meaning any attacker-supplied `ProofOfInclusion` with internally consistent (but fabricated) layers will pass `valid()` regardless of whether it corresponds to the actual committed tree root.

### Finding Description

The root cause is in `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`, in `ProofOfInclusion::valid()`:

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

        existing_hash = calculated_hash;   // ← set to layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← tautology
}
```

After the loop body, `existing_hash` has been set to `calculated_hash`, which was just verified to equal `layer.combined_hash`. The `root_hash()` method returns exactly `last.combined_hash`:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value as existing_hash after loop
    } else {
        self.node_hash
    }
}
```

Therefore `existing_hash == self.root_hash()` reduces to `last.combined_hash == last.combined_hash`, which is unconditionally `true`. The final guard adds zero verification.

**Exploit path:**

An attacker who can deliver a `ProofOfInclusion` to a verifying client (the struct is `Streamable` and exposed through Python bindings) constructs:

1. `node_hash = H(fake_key, fake_value)` — a leaf hash for a key-value pair that is **not** in the real tree.
2. A sequence of `ProofOfInclusionLayer` entries where each `combined_hash` is correctly computed from the previous hash and a chosen `other_hash`. The layers are internally consistent but rooted at an arbitrary, attacker-chosen hash.
3. Calls `proof.valid()` → returns `true`.

The proof does not correspond to the on-chain committed root, yet it passes the only verification method the API exposes.

By contrast, the consensus-layer Merkle set proof verification (`validate_merkle_proof` in `wheel/src/api.rs`) correctly accepts the trusted root as an explicit parameter and verifies against it. The DataLayer `ProofOfInclusion::valid()` has no equivalent parameter.

### Impact Explanation

A DataLayer client that receives a `ProofOfInclusion` from an untrusted DataLayer node and calls `proof.valid()` as its sole verification step will accept forged inclusion proofs. The attacker can convince the client that an arbitrary key-value pair is present in the tree when it is not. This directly satisfies the allowed impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."*

### Likelihood Explanation

`ProofOfInclusion` is `Streamable` and fully exposed through Python bindings (`py_get_proof_of_inclusion`, `py_valid`). The DataLayer protocol involves clients requesting proofs from potentially untrusted DataLayer nodes. The function name `valid()` strongly implies completeness, making it likely that callers rely on it exclusively without separately checking `proof.root_hash() == known_on_chain_root`. The fuzz target and all existing tests generate proofs from the real tree and immediately call `valid()`, so the missing root-binding check has never been exercised adversarially.

### Recommendation

Add a mandatory `expected_root: Hash` parameter to `valid()` (or introduce a separate `verify(root: Hash) -> bool` method) and replace the tautological final comparison with:

```rust
existing_hash == expected_root
```

This mirrors the correct pattern already used in the consensus-layer `validate_merkle_proof` function, which takes the trusted root as an explicit argument.

### Proof of Concept

```rust
use chia_datalayer::{Hash, ProofOfInclusion, ProofOfInclusionLayer, Side};

// Attacker constructs a forged proof for a fake leaf
let fake_node_hash: Hash = [0xAA; 32];
let fake_sibling: Hash = [0xBB; 32];

// Compute a consistent combined_hash (attacker controls both inputs)
let combined = chia_datalayer::calculate_internal_hash(
    &fake_node_hash,
    Side::Left,
    &fake_sibling,
);

let forged = ProofOfInclusion {
    node_hash: fake_node_hash,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Left,
        other_hash: fake_sibling,
        combined_hash: combined,   // internally consistent
    }],
};

// Passes valid() even though it corresponds to no real tree
assert!(forged.valid());
// forged.root_hash() == combined, which is NOT the on-chain committed root
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-29)
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
}
```

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L31-38)
```rust
impl ProofOfInclusion {
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

**File:** wheel/src/api.rs (L104-122)
```rust
#[pyfunction]
pub fn confirm_included_already_hashed(
    root: Bytes32,
    item: Bytes32,
    proof: &[u8],
) -> PyResult<bool> {
    validate_merkle_proof(proof, (&item).into(), (&root).into())
        .map_err(|_| PyValueError::new_err("Invalid proof"))
}

#[pyfunction]
pub fn confirm_not_included_already_hashed(
    root: Bytes32,
    item: Bytes32,
    proof: &[u8],
) -> PyResult<bool> {
    validate_merkle_proof(proof, (&item).into(), (&root).into())
        .map_err(|_| PyValueError::new_err("Invalid proof"))
        .map(|r| !r)
```
