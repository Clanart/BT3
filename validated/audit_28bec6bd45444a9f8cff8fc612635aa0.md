### Title
`ProofOfInclusion::valid()` Does Not Verify Against an External Root Hash — Forged Inclusion Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle library only verifies internal hash-chain consistency within the proof itself. The final check `existing_hash == self.root_hash()` is a tautology — `root_hash()` returns `last.combined_hash`, which is the same value `existing_hash` holds at loop exit. No external, trusted root is ever compared. Any attacker-crafted `ProofOfInclusion` that is internally self-consistent will pass `valid()`, regardless of which tree (or no real tree) it corresponds to.

### Finding Description

`ProofOfInclusion::valid()` iterates over layers, recomputing `calculate_internal_hash` and checking it equals `layer.combined_hash`: [1](#0-0) 

After the loop, `existing_hash` equals the last `layer.combined_hash` (enforced by the in-loop check). `root_hash()` is defined as: [2](#0-1) 

It returns `last.combined_hash` — the same value. So the final line:

```rust
existing_hash == self.root_hash()
```

is always `true` when the loop completes. `valid()` never compares against any externally-supplied, trusted root hash. It only checks that the proof's own fields are mutually consistent.

`ProofOfInclusion` is `Streamable` and fully deserializable from bytes: [3](#0-2) 

It is exposed to Python via `py_valid` and `from_bytes`: [4](#0-3) 

The Python type stub confirms the public API: [5](#0-4) 

### Impact Explanation

An attacker who can deliver a serialized `ProofOfInclusion` to a DataLayer client can forge proof of inclusion for any key-value pair in any tree root of their choosing. The client calls `proof.valid()`, receives `true`, and believes the key-value pair is present in the tree — even though it is not. This allows untrusted input to prove invalid DataLayer state, matching the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."*

### Likelihood Explanation

The `valid()` method is the sole public API for proof verification. Its name implies complete validation. Any DataLayer consumer that calls `proof.valid()` without separately checking `proof.root_hash() == known_root` is vulnerable. The Python binding makes this pattern easy to reach from application code. The `ProofOfInclusion` struct is `Streamable`, so it can be received over the network and deserialized without any structural rejection of attacker-controlled values.

### Recommendation

`valid()` must accept a trusted external root hash parameter and compare against it:

```rust
pub fn valid_against_root(&self, expected_root: &Hash) -> bool {
    // ... existing chain check ...
    existing_hash == *expected_root
}
```

Alternatively, rename the current method to `is_internally_consistent()` and add a separate `valid(expected_root: &Hash) -> bool` that performs the root comparison. Remove or deprecate the no-argument form from all public APIs (Rust and Python/wasm bindings).

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, KeyId, ValueId, MerkleBlob
import hashlib

def sha256_prefix(prefix: bytes, *parts: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(prefix)
    for p in parts:
        h.update(p)
    return h.digest()

# Craft a fake leaf hash and a fake sibling hash
fake_leaf  = bytes([0xAA] * 32)
fake_sibling = bytes([0xBB] * 32)

# Compute a fake combined_hash that is internally consistent
fake_combined = sha256_prefix(b"\x02", fake_leaf, fake_sibling)

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right → sibling is on the right
    other_hash=fake_sibling,
    combined_hash=fake_combined,
)

proof = ProofOfInclusion(node_hash=fake_leaf, layers=[layer])

# valid() returns True even though this proof was never generated
# from any real MerkleBlob and corresponds to no real tree root
assert proof.valid(), "Forged proof accepted!"
print("root_hash reported by forged proof:", proof.root_hash().hex())
# Caller who only checks proof.valid() is deceived.
```

The `valid()` call succeeds because `calculate_internal_hash(fake_leaf, Right, fake_sibling) == fake_combined` is true by construction, and `root_hash()` returns `fake_combined` — the same value — making the final equality trivially true. [1](#0-0) [6](#0-5)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L236-244)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...

```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-62)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
}
```
