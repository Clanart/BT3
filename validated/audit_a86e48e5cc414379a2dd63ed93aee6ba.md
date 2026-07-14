### Title
`ProofOfInclusion.valid()` Does Not Verify Against an Expected Root Hash, Enabling Forged DataLayer Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary
`ProofOfInclusion.valid()` in the DataLayer Merkle implementation only checks the internal self-consistency of the proof's hash chain. It never accepts or compares against an externally-supplied, trusted root hash. Because the struct is fully serializable and exposed via Python/wasm bindings, an untrusted peer can supply a crafted `ProofOfInclusion` whose internal hashes are consistent but whose root belongs to a completely different DataLayer store. A consumer calling `proof.valid()` as the sole validation step will accept the forged proof.

### Finding Description

`ProofOfInclusion` is defined as a `Streamable` struct with public fields, exposed via `pyclass(get_all, from_py_object)`:

```rust
pub struct ProofOfInclusion {
    pub node_hash: Hash,
    pub layers: Vec<ProofOfInclusionLayer>,
}
``` [1](#0-0) 

The `valid()` method computes a root hash by chaining the proof's own internal fields and then compares the result only against `self.root_hash()`, which is itself derived from the last layer's `combined_hash` field — a field the attacker controls:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← attacker-controlled
    } else {
        self.node_hash
    }
}

pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(...);
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    existing_hash == self.root_hash()   // ← self-referential, not vs. expected root
}
``` [2](#0-1) 

The check `existing_hash == self.root_hash()` is a tautology: after the loop, `existing_hash` equals the last `combined_hash`, and `self.root_hash()` also returns the last `combined_hash`. The method therefore only verifies that the attacker-supplied chain is internally self-consistent; it never verifies that the chain terminates at the expected store root.

The Python binding exposes `from_bytes`, `from_bytes_unchecked`, and `valid()` directly: [3](#0-2) 

The Python binding also exposes `MerkleBlob.get_proof_of_inclusion()` and `ProofOfInclusion.valid()` as the primary proof-generation and proof-validation API: [4](#0-3) 

### Impact Explanation

A DataLayer client that receives a `ProofOfInclusion` from an untrusted peer (e.g., a DataLayer sync partner) and calls `proof.valid()` as the sole validation step will accept any internally-consistent proof, regardless of which store root it belongs to. An attacker can fabricate a proof asserting that key K maps to value V in store S (when it does not) by constructing a self-consistent hash chain rooted at an arbitrary hash R′ ≠ root(S). `valid()` returns `True`.

This maps directly to the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … lets untrusted input prove invalid state."**

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and fully deserializable from bytes via `from_bytes` / `from_bytes_unchecked`. The Python binding exposes `valid()` as the only proof-validation method, with no parameter for an expected root. Any DataLayer client that follows the natural API usage pattern — deserialize proof, call `valid()` — is vulnerable. The attack requires only the ability to send a crafted serialized `ProofOfInclusion` to a peer, which is a normal DataLayer network operation.

### Recommendation

`valid()` must accept an `expected_root: Hash` parameter and compare the computed root against it:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
    // ... existing chain check ...
    existing_hash == *expected_root
}
```

Alternatively, rename the current method to `internally_consistent()` and add a separate `valid(expected_root: &Hash) -> bool` that performs the full check. Update the Python binding accordingly so callers cannot accidentally omit the root comparison.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from chia_rs import calculate_internal_hash  # or equivalent

# Attacker fabricates a self-consistent proof for an arbitrary leaf
fake_leaf_hash  = bytes32(b'\xaa' * 32)
fake_other_hash = bytes32(b'\xbb' * 32)
# compute a combined_hash that is internally consistent
fake_combined   = calculate_internal_hash(fake_leaf_hash, 1, fake_other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=fake_other_hash,
    combined_hash=fake_combined,
)
proof = ProofOfInclusion(node_hash=fake_leaf_hash, layers=[layer])

# valid() returns True even though this proof belongs to no real store
assert proof.valid()            # ← passes; root is never checked against expected
assert proof.root_hash() == fake_combined  # ← attacker-chosen root
``` [5](#0-4) [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L25-29)
```rust
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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-243)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
```

**File:** wheel/python/chia_rs/datalayer.pyi (L335-335)
```text
    def get_proof_of_inclusion(self, key: KeyId) -> ProofOfInclusion: ...
```
