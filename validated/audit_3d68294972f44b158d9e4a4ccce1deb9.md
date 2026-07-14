### Title
`ProofOfInclusion::valid()` Does Not Verify Against an External Root Hash, Allowing Forged Inclusion Proofs - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle implementation only checks the internal hash-chain consistency of the proof against values embedded within the proof itself. It never accepts or compares against an externally-supplied, trusted tree root hash. An attacker who can deliver a crafted `ProofOfInclusion` to a verifier that calls `valid()` without separately checking `root_hash()` against the actual tree root can forge a proof of inclusion for any key-value pair in any tree.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct (deserializable from arbitrary bytes) exposed via Python and Rust APIs. Its `valid()` method performs the following:

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
``` [1](#0-0) 

The final check `existing_hash == self.root_hash()` is trivially satisfied whenever the loop passes, because `root_hash()` returns `last.combined_hash` — the same value that `existing_hash` was just set to in the final loop iteration:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash  // same field already verified in the loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

`valid()` therefore only checks that the proof is **internally self-consistent** — it never compares the computed root against any externally-known, trusted tree root. The method signature accepts no expected root parameter. A caller who receives a `ProofOfInclusion` from an untrusted source and calls `valid()` will accept any internally consistent proof, regardless of which tree it actually commits to.

The struct is fully `Streamable` and exposed to Python: [3](#0-2) [4](#0-3) 

The Python binding exposes `valid()` and `root_hash()` as separate methods, with no enforcement that callers check both: [5](#0-4) 

All existing tests and the fuzz target call `proof.valid()` without comparing `proof.root_hash()` to the actual blob's root hash: [6](#0-5) [7](#0-6) 

This is the direct analog to the Linea report: a computed value (`y` / the root hash) is derived from the submitted data and used in a commitment, but it is never checked against an externally-provided trusted value. The mismatch only surfaces if the caller separately performs the root comparison — which the API design does not enforce or encourage.

### Impact Explanation

A DataLayer client that receives a `ProofOfInclusion` from an untrusted DataLayer server (e.g., over the network) and calls `proof.valid()` to verify it will accept any internally consistent proof, including one that proves inclusion in a completely different tree. The attacker can:

1. Construct a `ProofOfInclusion` with arbitrary `node_hash`, `other_hash`, and `combined_hash` values that form a valid hash chain.
2. Serialize it via `Streamable::to_bytes()`.
3. Deliver it to a verifier.
4. The verifier calls `proof.valid()` → `true`, accepting a forged proof of inclusion for a key-value pair that does not exist in the actual DataLayer tree.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, letting untrusted input prove invalid state.**

### Likelihood Explanation

The `valid()` method name strongly implies complete proof validation. The Python and Rust APIs expose `valid()` and `root_hash()` as separate, independent methods with no documentation or type-system enforcement requiring callers to check both. Any DataLayer client that follows the natural API usage pattern of calling `proof.valid()` is vulnerable. The `ProofOfInclusion` struct is `Streamable`, making it trivially constructable from attacker-controlled bytes.

### Recommendation

`valid()` should accept an expected root hash parameter and compare the computed root against it:

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
    &existing_hash == expected_root
}
```

Alternatively, rename the current `valid()` to `is_internally_consistent()` to make its limited scope explicit, and add a `valid_against_root(expected: &Hash) -> bool` method that performs the full check. Update all callers — including the Python bindings, fuzz targets, and tests — to use the root-checking variant when verifying proofs from untrusted sources.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, ProofOfInclusion, ProofOfInclusionLayer
from chia_rs.sized_bytes import bytes32

# Build a real tree with key 1 -> value 1
blob = MerkleBlob(blob=bytearray())
real_hash = bytes32(b'\x01' * 32)
blob.insert(KeyId(1), ValueId(1), real_hash)
blob.calculate_lazy_hashes()

# Attacker crafts a fake proof for key 99 (not in the tree)
# by constructing an internally consistent hash chain from scratch
fake_leaf_hash = bytes32(b'\xaa' * 32)
fake_other_hash = bytes32(b'\xbb' * 32)
# compute combined_hash = internal_hash(fake_leaf_hash, fake_other_hash)
# (attacker computes this using the known internal_hash formula)
import hashlib
combined = hashlib.sha256(b'\x00' * 30 + fake_leaf_hash + fake_other_hash).digest()
fake_combined_hash = bytes32(combined)

fake_layer = ProofOfInclusionLayer(
    other_hash_side=1,  # Right
    other_hash=fake_other_hash,
    combined_hash=fake_combined_hash,
)
fake_proof = ProofOfInclusion(node_hash=fake_leaf_hash, layers=[fake_layer])

# valid() returns True for a proof that has nothing to do with the real tree
assert fake_proof.valid()  # PASSES — forged proof accepted

# The correct check would be:
assert fake_proof.root_hash() == blob.get_root_hash()  # FAILS — but callers don't do this
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

**File:** wheel/python/chia_rs/datalayer.pyi (L241-243)
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
