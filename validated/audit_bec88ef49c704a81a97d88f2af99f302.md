### Title
Tautological Final Check in `ProofOfInclusion::valid()` Allows Forged Inclusion Proofs to Pass Validation — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` performs only internal self-consistency checks on the proof chain. Its final assertion `existing_hash == self.root_hash()` is a tautology: after the loop, `existing_hash` always equals `self.root_hash()` by construction. No external tree root is ever compared. An attacker can craft a `ProofOfInclusion` (via the `Streamable` deserialization path) with an arbitrary `node_hash` and internally consistent layers, and `valid()` will return `true` — falsely certifying that any key-value pair is present in the DataLayer store.

### Finding Description

`ProofOfInclusion` is defined as a `Streamable` struct with two fields: `node_hash` (the claimed leaf hash) and `layers` (a chain of `ProofOfInclusionLayer` values, each holding `other_hash_side`, `other_hash`, and `combined_hash`). [1](#0-0) 

The `root_hash()` helper returns the `combined_hash` of the last layer (or `node_hash` if there are no layers): [2](#0-1) 

`valid()` iterates over layers, verifying that each `combined_hash` equals the hash computed from the running hash and the sibling. After the loop, `existing_hash` holds the last `calculated_hash`, which the loop already confirmed equals `layer.combined_hash`. The final line then compares `existing_hash` against `self.root_hash()`, which returns that same `last.combined_hash`: [3](#0-2) 

The final check `existing_hash == self.root_hash()` is therefore always `true` after the loop completes without an early return. `valid()` never compares the computed root against any externally known, trusted tree root. The same flaw applies to the empty-layers case: `valid()` returns `true` for any `ProofOfInclusion { node_hash: X, layers: vec![] }` regardless of what `X` is.

This method is exposed directly to Python callers: [4](#0-3) 

Because `ProofOfInclusion` derives `Streamable`, it can be deserialized from arbitrary bytes received over the network: [5](#0-4) 

### Impact Explanation

A DataLayer client that receives a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` to decide whether a key-value pair is present in the store will accept any internally consistent forged proof. The attacker chooses an arbitrary `node_hash` (e.g., the hash of a key-value pair not in the store), constructs a chain of layers whose hashes are self-consistent (trivially computable), and the client accepts the forged inclusion proof. This directly enables untrusted input to prove invalid state in the DataLayer Merkle tree, matching the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

### Likelihood Explanation

The `ProofOfInclusion` type is `Streamable` and exposed via Python bindings. Any DataLayer client that relies on `valid()` as the sole verification step — a natural assumption given the method name — is vulnerable. The forge requires only arithmetic hash computation; no privileged access, key material, or chain reorg is needed.

### Recommendation

`valid()` must accept a trusted external root hash and compare against it. The tautological final check should be replaced with a comparison against the caller-supplied root:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
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

    &existing_hash == expected_root
}
```

The Python binding `py_valid` and all call sites (including the test at line 123 and line 156 of `proof_of_inclusion.rs`) must be updated to supply the known tree root.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};
use chia_datalayer::merkle::blob::{calculate_internal_hash, internal_hash};

// Attacker wants to forge a proof that `fake_leaf` is in the tree.
let fake_leaf = Hash(/* any 32-byte value */);
let fake_sibling = Hash(/* any 32-byte value */);

// Compute a self-consistent combined_hash for one layer.
let combined = calculate_internal_hash(&fake_leaf, Side::Right, &fake_sibling);

let forged = ProofOfInclusion {
    node_hash: fake_leaf,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Right,
        other_hash: fake_sibling,
        combined_hash: combined,
    }],
};

// valid() returns true even though fake_leaf is not in any real tree.
assert!(forged.valid());
```

The `combined_hash` in the single layer equals `existing_hash` after the loop, so `existing_hash == self.root_hash()` is trivially satisfied. The proof passes with no knowledge of the actual DataLayer tree root.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-18)
```rust
#[derive(Clone, Debug, std::hash::Hash, Eq, PartialEq, Streamable)]
pub struct ProofOfInclusionLayer {
    pub other_hash_side: Side,
    pub other_hash: Hash,
    pub combined_hash: Hash,
}
```

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L25-29)
```rust
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
