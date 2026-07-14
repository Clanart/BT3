### Title
`ProofOfInclusion::valid()` Never Validates Against an External Root — Forged Inclusion Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only checks the internal self-consistency of the proof chain. It never compares the computed root against any external, trusted root hash. The final comparison in the function is a mathematical tautology — it always evaluates to `true` when the loop completes without error. As a result, any attacker who can supply a `ProofOfInclusion` object (via the Python or Rust API) can fabricate a structurally consistent proof for any arbitrary `node_hash` they choose, and `valid()` will return `true`, regardless of whether that node is actually present in the DataLayer tree.

### Finding Description

In `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`, the `ProofOfInclusion` struct has two methods:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // <-- returns the last layer's combined_hash
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

        existing_hash = calculated_hash;  // existing_hash = layer.combined_hash
    }

    existing_hash == self.root_hash()  // TAUTOLOGY: both sides are last.combined_hash
}
```

After the loop completes without returning `false`, `existing_hash` holds the value of the last `calculated_hash`, which was already asserted equal to `layer.combined_hash` for the last layer. `self.root_hash()` also returns `last.combined_hash`. The final comparison `existing_hash == self.root_hash()` is therefore always `true` — it compares a value to itself.

The function never accepts a trusted external root as a parameter and never compares the proof's computed root against one. An attacker can construct a `ProofOfInclusion` with:
- An arbitrary `node_hash` (e.g., the hash of a key-value pair that does not exist in the tree)
- A chain of `ProofOfInclusionLayer` entries where each `combined_hash` is correctly computed from the previous hash and a chosen `other_hash`

This fabricated proof will pass `valid()` unconditionally.

The struct is fully exposed to Python via `#[pymethods]` and the `py_valid()` binding, and is also `Streamable` (deserializable from bytes), making it directly reachable from untrusted network input.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any verifier — Python code in the Chia full node, a DataLayer client, or a third-party integrator — that calls `proof.valid()` to authenticate a proof received from an untrusted peer will accept a completely fabricated proof. The attacker can prove that any arbitrary key-value pair is included in a DataLayer store, even if it is not. This enables:

- Forged state proofs: an attacker proves a key exists in a DataLayer store when it does not, causing downstream logic to act on false data.
- Forged exclusion bypass: if the caller uses `valid()` as the sole gate for DataLayer state verification, the attacker bypasses the entire Merkle integrity guarantee.

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and exposed via Python bindings. Any code path that deserializes a `ProofOfInclusion` from the network and calls `.valid()` without also independently checking `.root_hash() == known_trusted_root` is vulnerable. The method name `valid()` strongly implies it performs complete proof verification, making it highly likely to be misused as the sole verification gate. The Python binding `py_valid()` is the primary interface for DataLayer proof verification in the Chia ecosystem.

### Recommendation

The `valid()` method must accept a trusted external root hash as a parameter and compare the computed root against it:

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
    &existing_hash == trusted_root  // compare against external trusted root
}
```

The existing `valid()` method (without a root parameter) should be removed or deprecated to prevent misuse. The Python binding should be updated accordingly.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
import hashlib

# Attacker-chosen fake node hash (not in any real tree)
fake_node_hash = bytes([0xAB] * 32)

# Build a single-layer proof with a chosen other_hash
other_hash = bytes([0xCD] * 32)

# Compute combined_hash = calculate_internal_hash(fake_node_hash, side=Left, other_hash)
# (using the same hash function as chia-datalayer)
# For demonstration, assume Side.Left means hash(fake_node_hash + other_hash)
combined = hashlib.sha256(fake_node_hash + other_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=0,   # Left
    other_hash=other_hash,
    combined_hash=combined,
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True even though fake_node_hash is not in any real tree
assert forged_proof.valid() == True
# root_hash() returns `combined`, which is not the real tree root
# but valid() never checks this
```

The tautology is confirmed at: [1](#0-0) 

The Python binding that exposes this to untrusted input: [2](#0-1) 

The `Streamable` derive that makes `ProofOfInclusion` deserializable from arbitrary bytes: [3](#0-2)

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
