### Title
`ProofOfInclusion::valid()` Performs Only Self-Referential Consistency Check, Not Root-Binding Verification — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` is the sole validation method on the `ProofOfInclusion` type and is exposed directly to Python and WASM consumers. Its final check — `existing_hash == self.root_hash()` — is a tautology: after the loop, `existing_hash` always equals `self.layers.last().combined_hash`, which is exactly what `root_hash()` returns. The method therefore only verifies internal chain consistency and never binds the proof to any external trusted root. An attacker can craft a `ProofOfInclusion` that passes `valid()` while proving inclusion in an entirely different tree.

### Finding Description

`ProofOfInclusion` is a `Streamable` type (deserializable from arbitrary bytes via `from_bytes` / `parse_rust`) and is exposed to Python via `pyclass` and `PyStreamable`. [1](#0-0) 

Its `root_hash()` helper returns the `combined_hash` of the last layer (or `node_hash` for a single-node proof): [2](#0-1) 

`valid()` iterates the layers, checking that each `combined_hash` equals the hash computed from the running hash and the sibling, then ends with: [3](#0-2) 

After the loop body, `existing_hash` has been set to `calculated_hash`, and the loop only continued because `calculated_hash == layer.combined_hash`. So on exit, `existing_hash` is identically `self.layers.last().combined_hash` — the same value `root_hash()` returns. The final comparison is always `true` when the loop completes without an early return. No external trusted root is ever consulted.

The Python stub exposes `valid()` and `root_hash()` as independent methods with no documented ordering requirement: [4](#0-3) 

The fuzz target and all internal tests call only `proof.valid()` without comparing `root_hash()` to any trusted value: [5](#0-4) 

### Impact Explanation

Any DataLayer consumer that deserializes a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` as its sole check will accept a proof that is internally self-consistent but anchored to an attacker-chosen root — not the authoritative DataLayer root. This allows an attacker to forge inclusion proofs for arbitrary key-value pairs against any tree root they choose, satisfying the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, letting untrusted input prove invalid state.**

### Likelihood Explanation

The API design actively encourages the unsafe pattern. `valid()` is the only method named as a correctness predicate; `root_hash()` is a plain accessor with no indication that callers must compare it to a trusted value. The fuzz target and all Rust tests use `proof.valid()` alone, establishing this as the idiomatic usage. Python consumers receiving proofs over the network have no in-API signal that a second check is required.

### Recommendation

Replace the self-referential final check with a mandatory trusted-root parameter, or add a separate method that makes the binding explicit:

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
    &existing_hash == trusted_root   // bind to external trusted root
}
```

Deprecate or remove the root-free `valid()` from the public API, or at minimum add a `#[must_use]` doc comment warning that `root_hash()` must be compared to a trusted value after calling `valid()`.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

fn calculate_internal_hash(left: &Hash, right: &Hash) -> Hash { /* ... */ }

fn forge_proof_for_arbitrary_key() {
    // Attacker-chosen leaf hash (claims to prove any key they want)
    let fake_node_hash: Hash = [0xAA; 32].into();
    let fake_sibling:   Hash = [0xBB; 32].into();
    // Build a single internally-consistent layer anchored to an attacker root
    let attacker_root = calculate_internal_hash(&fake_node_hash, &fake_sibling);

    let forged = ProofOfInclusion {
        node_hash: fake_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Right,
            other_hash: fake_sibling,
            combined_hash: attacker_root,   // attacker controls this root
        }],
    };

    // Passes valid() — no trusted root is checked
    assert!(forged.valid());

    // root_hash() returns the attacker-chosen root, not the real DataLayer root
    assert_eq!(forged.root_hash(), attacker_root);

    // A consumer that only calls proof.valid() accepts this as a legitimate
    // inclusion proof for fake_node_hash in the real DataLayer store.
}
```

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L20-29)
```rust
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

**File:** wheel/python/chia_rs/datalayer.pyi (L242-243)
```text
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
