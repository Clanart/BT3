### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Trusted Root Hash, Accepting Forged Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks the internal self-consistency of the proof chain. Its final comparison is a tautology — it compares `existing_hash` against `self.root_hash()`, which is derived from the proof itself, not from any external trusted tree root. An attacker can construct a fully fabricated `ProofOfInclusion` for any arbitrary `node_hash` and it will pass `valid()`.

### Finding Description

The `valid()` method in `ProofOfInclusion` iterates over each `ProofOfInclusionLayer`, verifying that each layer's `combined_hash` is correctly computed from the running hash and `other_hash`. After the loop, `existing_hash` holds the last `calculated_hash`, which was already asserted equal to `layer.combined_hash` inside the loop. The final guard:

```rust
existing_hash == self.root_hash()
```

is a tautology because `root_hash()` returns `self.layers.last().combined_hash` — the exact value `existing_hash` was just set to. The function never compares against any externally supplied, trusted Merkle root.

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
        existing_hash = calculated_hash;   // existing_hash == layer.combined_hash
    }
    existing_hash == self.root_hash()      // root_hash() == layers.last().combined_hash
                                           // → always true after the loop
}
``` [1](#0-0) 

`root_hash()` returns the proof-internal value, not a caller-supplied trusted root:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

`ProofOfInclusion` is a `Streamable` type (deserializable from bytes) and is exposed to Python via `py_valid()`: [3](#0-2) 

The fuzz target and all tests call only `proof.valid()` with no separate root-hash comparison, establishing this as the intended verification API: [4](#0-3) 

### Impact Explanation

Any party that receives a `ProofOfInclusion` from an untrusted source (network peer, Python caller, deserialized bytes) and calls `valid()` as the sole verification step will accept a completely fabricated proof. The attacker can claim any `node_hash` is present in the DataLayer tree without it actually being there. This matches the allowed impact: **DataLayer Merkle proof logic accepts forged inclusion proofs, letting untrusted input prove invalid state.**

### Likelihood Explanation

`ProofOfInclusion` is `Streamable` and Python-exposed. Any DataLayer client that deserializes a proof from an untrusted peer and calls `proof.valid()` — the only verification method the API exposes — is vulnerable. The misleading name `valid()` makes it highly likely that callers treat it as a complete verification rather than a partial one.

### Recommendation

`valid()` must accept a caller-supplied trusted root and compare against it:

```rust
pub fn valid(&self, trusted_root: &Hash) -> bool {
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

All call sites — including the Python binding `py_valid()` and the fuzz target — must be updated to supply the trusted root obtained from the `MerkleBlob` or a verified on-chain commitment.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

fn forge_proof(claimed_node_hash: Hash) -> ProofOfInclusion {
    let fake_other_hash = [0xAB_u8; 32].into();
    // compute combined_hash so the chain is internally consistent
    let combined = chia_datalayer::calculate_internal_hash(
        &claimed_node_hash,
        Side::Left,
        &fake_other_hash,
    );
    ProofOfInclusion {
        node_hash: claimed_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Left,
            other_hash: fake_other_hash,
            combined_hash: combined,
        }],
    }
}

fn main() {
    let forged = forge_proof([0xFF_u8; 32].into());
    // passes — no trusted root is checked
    assert!(forged.valid());
}
```

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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
