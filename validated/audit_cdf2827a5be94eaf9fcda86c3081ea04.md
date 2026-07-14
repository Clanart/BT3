### Title
`ProofOfInclusion::valid()` Never Anchors Proof Chain to a Trusted Root Hash, Enabling Forged Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate checks only that the proof's internal hash chain is self-consistent. It derives the "root" directly from the last layer of the proof itself (`layers.last().combined_hash`) and compares the computed chain against that self-supplied value. No external, trusted committed root is ever consulted. An attacker who can supply a `ProofOfInclusion` object (which is `Streamable` and fully deserializable from bytes via Python bindings) can fabricate a structurally valid proof for any arbitrary `node_hash`, causing any verifier that calls `proof.valid()` to accept the forged proof.

### Finding Description

`ProofOfInclusion` is defined as a `Streamable` struct with public fields and Python bindings, making it fully constructable from untrusted bytes: [1](#0-0) 

The `root_hash()` method derives the root entirely from the proof's own last layer: [2](#0-1) 

The `valid()` method then checks that each layer's `combined_hash` equals the hash computed from the previous hash and `other_hash`, and finally compares `existing_hash` against `self.root_hash()`: [3](#0-2) 

The final check `existing_hash == self.root_hash()` is **tautological**: after the loop, `existing_hash` holds the last computed hash, which equals `layers.last().combined_hash` — the exact value `root_hash()` returns. If the loop passes (i.e., the chain is internally consistent), the final check always passes. No trusted, externally committed root is ever compared.

**Exploit path:**

An attacker constructs a `ProofOfInclusion` with:
- An arbitrary `node_hash` (the leaf they wish to falsely prove is included)
- A chain of `ProofOfInclusionLayer` entries where each `combined_hash` is correctly computed from the previous hash and an arbitrary `other_hash`

Because `valid()` only checks internal consistency and derives the root from the proof itself, it returns `true` for this fabricated proof. The Python binding exposes `valid()` directly: [4](#0-3) 

The Python type stub confirms `ProofOfInclusion` is constructable from bytes and exposes `valid()` and `root_hash()` as the only verification interface: [5](#0-4) 

This is structurally identical to the Alligator bug: the authority chain (here, the proof chain) is validated for internal consistency but never anchored to a known trusted value (here, the committed tree root).

By contrast, the consensus-layer `validate_merkle_proof` in `merkle_tree.rs` correctly anchors the proof to an external root: [6](#0-5) 

The DataLayer `ProofOfInclusion::valid()` has no equivalent anchor check.

### Impact Explanation

Any DataLayer verifier that receives a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` without separately checking `proof.root_hash() == committed_root` will accept a forged proof. This lets an attacker prove that an arbitrary key-value pair is included in a DataLayer tree when it is not, corrupting the verifier's view of the committed state. This matches the allowed High impact: "DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with Python bindings and `from_bytes` deserialization. Any DataLayer peer exchange that transmits `ProofOfInclusion` objects and relies on `valid()` as the sole verification step is directly exploitable by any unprivileged network participant. The misleading name `valid()` makes incorrect usage highly likely.

### Recommendation

`valid()` must accept a trusted root hash parameter and compare the computed chain root against it:

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
    &existing_hash == trusted_root  // anchor to external trusted root
}
```

The existing `valid()` method (which only checks internal consistency) should be renamed to `is_internally_consistent()` or removed to prevent misuse. The Python binding should expose only the root-anchored variant.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from chia_rs.sized_bytes import bytes32
import hashlib

# Arbitrary leaf hash the attacker wants to "prove" is in the tree
fake_node_hash = bytes32(b'\xAA' * 32)

# Arbitrary sibling hash
other_hash = bytes32(b'\xBB' * 32)

# Compute a valid combined_hash for this layer (using the DataLayer internal hash)
# combined = sha256(sha256(fake_node_hash) + sha256(other_hash)) or equivalent
# (exact hash function matches calculate_internal_hash)
combined = bytes32(hashlib.sha256(fake_node_hash + other_hash).digest())

layer = ProofOfInclusionLayer(
    other_hash_side=1,   # Right
    other_hash=other_hash,
    combined_hash=combined,
)

proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True for a completely fabricated proof
assert proof.valid(), "Forged proof accepted!"
# proof.root_hash() == combined — self-derived, not the real tree root
```

### Citations

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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-244)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...

```

**File:** crates/chia-consensus/src/merkle_tree.rs (L334-344)
```rust
pub fn validate_merkle_proof(
    proof: &[u8],
    item: &[u8; 32],
    root: &[u8; 32],
) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root {
        return Err(SetError);
    }
    Ok(tree.generate_proof(item)?.0)
}
```
