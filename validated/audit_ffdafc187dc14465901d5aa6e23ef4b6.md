### Title
`ProofOfInclusion::valid()` Tautological Final Check Accepts Forged DataLayer Merkle Proofs — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` performs only an internal self-consistency check on the hash chain it carries. Its final comparison `existing_hash == self.root_hash()` is a tautology: after the loop, `existing_hash` is always equal to `self.root_hash()` by construction. No external trusted root is ever compared. Any attacker who can supply a `ProofOfInclusion` object — via the Python/wasm bindings or over the DataLayer wire — can forge a proof for arbitrary data that passes `valid()`.

### Finding Description

`ProofOfInclusion` is defined in `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs` and exposed to Python via `pymethods`. [1](#0-0) 

`root_hash()` returns the `combined_hash` field of the **last layer stored inside the proof itself**: [2](#0-1) 

`valid()` iterates the layers, verifying that each `combined_hash` equals `calculate_internal_hash(existing_hash, side, other_hash)`, then sets `existing_hash = calculated_hash`. After the loop, `existing_hash` holds the last `calculated_hash`, which the loop already asserted equals the last `layer.combined_hash`. The final guard is: [3](#0-2) 

```
existing_hash  ==  self.root_hash()
last.combined_hash  ==  last.combined_hash   // always true
```

This is a tautology. `valid()` never compares the computed root against any externally-supplied, trusted root hash. It only verifies that the attacker-supplied layers are internally self-consistent.

Because `ProofOfInclusion` is `Streamable` and exposed via Python bindings, an attacker can deserialize or construct an arbitrary `ProofOfInclusion` with any `node_hash` (representing any key-value pair) and any consistent chain of `ProofOfInclusionLayer` values, and `valid()` will return `true`. [4](#0-3) 

The fuzz target and all tests call `proof.valid()` without comparing `proof.root_hash()` against an external root, confirming this is the intended API usage pattern: [5](#0-4) [6](#0-5) 

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any consumer of the DataLayer API that calls `proof.valid()` as its sole verification step — which is exactly what the API design and all existing call sites encourage — will accept a completely fabricated proof of inclusion for any key-value pair. An attacker can prove that arbitrary data exists in a DataLayer store whose actual root they do not control, enabling unauthorized state acceptance across DataLayer clients.

### Likelihood Explanation

`ProofOfInclusion` is `Streamable` and exposed via Python bindings. DataLayer proofs are exchanged between nodes over the network. Any node that receives a proof from an untrusted peer and calls `proof.valid()` is vulnerable. No privileged access is required; the attacker only needs to send a crafted serialized `ProofOfInclusion`.

### Recommendation

`valid()` must accept an external trusted root hash and compare against it instead of `self.root_hash()`:

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
    &existing_hash == trusted_root
}
```

All call sites must supply the root hash obtained from a trusted local source (e.g., `merkle_blob.get_root_hash()`), not from the proof itself.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
from chia_rs.sized_bytes import bytes32
import hashlib

# Forge a proof for an arbitrary node_hash not in any real tree.
fake_node_hash = bytes32(b"\xde\xad" * 16)
fake_other_hash = bytes32(b"\xbe\xef" * 16)

# Compute a consistent combined_hash so the layer passes the internal check.
# calculate_internal_hash concatenates hashes in side order and SHA-256s them.
# Side.Left means: sha256(fake_node_hash || fake_other_hash)
combined = hashlib.sha256(fake_node_hash + fake_other_hash).digest()
fake_combined_hash = bytes32(combined)

layer = ProofOfInclusionLayer(
    other_hash_side=Side.Right,   # fake_node_hash is on the left
    other_hash=fake_other_hash,
    combined_hash=fake_combined_hash,
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True for a completely fabricated proof.
assert forged_proof.valid(), "Expected forged proof to pass valid()"
# root_hash() returns the attacker-controlled combined_hash, not any real tree root.
assert forged_proof.root_hash() == fake_combined_hash
print("Forged proof accepted by valid():", forged_proof.valid())
```

The forged proof passes `valid()` because the final check `existing_hash == self.root_hash()` reduces to `fake_combined_hash == fake_combined_hash`, which is always true regardless of whether `fake_node_hash` exists in any real DataLayer tree.

### Citations

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L115-124)
```rust
            for kv_id in keys_values.keys().copied() {
                let proof_of_inclusion = match merkle_blob.get_proof_of_inclusion(kv_id) {
                    Ok(proof_of_inclusion) => proof_of_inclusion,
                    Err(error) => {
                        open_dot(merkle_blob.to_dot().unwrap().set_note(&error.to_string()));
                        panic!("here");
                    }
                };
                assert!(proof_of_inclusion.valid());
            }
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
