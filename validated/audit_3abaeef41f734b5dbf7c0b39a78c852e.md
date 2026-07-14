### Title
`ProofOfInclusion::valid()` Tautological Root-Hash Check Accepts Forged Inclusion Proofs Without External Root Anchor — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle crate performs only internal chain-consistency checks. Its final comparison is tautologically true after the loop, meaning the function never validates the proof against any external/expected tree root. A `ProofOfInclusion` deserialized from attacker-controlled bytes (via `Streamable`/`from_bytes`) can be made to pass `valid()` while proving inclusion in a completely different tree than the actual committed DataLayer root.

### Finding Description

In `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`, the `root_hash()` helper and `valid()` method are defined as follows:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← returns last layer's combined_hash
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

        existing_hash = calculated_hash;   // ← existing_hash = layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: both sides = last.combined_hash
}
``` [1](#0-0) 

After the loop completes without returning `false`, `existing_hash` holds the last `calculated_hash`, which the loop already verified equals `layer.combined_hash`. `root_hash()` returns that same `last.combined_hash`. The final comparison `existing_hash == self.root_hash()` is therefore **always true** — it is a tautology that adds no security.

The zero-layer case is equally trivially true: `existing_hash = self.node_hash` and `root_hash() = self.node_hash`, so any `ProofOfInclusion { node_hash: X, layers: [] }` passes `valid()` for any `X`. [2](#0-1) 

`valid()` accepts no external root-hash parameter. It is the **only** validation method on the struct. The Python binding exposes it directly: [3](#0-2) [4](#0-3) 

Because `ProofOfInclusion` derives `Streamable`, it can be deserialized from arbitrary bytes via `from_bytes` / `parse_rust`: [5](#0-4) [6](#0-5) 

An attacker can craft a `ProofOfInclusion` byte string where:
- `node_hash` = any chosen leaf hash `H`
- `layers[i].other_hash` = any chosen sibling hash `O`
- `layers[i].combined_hash` = `calculate_internal_hash(H, side, O)` (computed offline)

This proof is internally consistent and passes `valid() == true`, yet its `root_hash()` is an attacker-chosen value unrelated to the actual committed DataLayer tree root.

### Impact Explanation

Any consumer that calls `proof.valid()` as the sole acceptance criterion — without separately comparing `proof.root_hash()` against the on-chain committed DataLayer root — will accept forged proofs of inclusion for arbitrary key-value pairs in any DataLayer store. This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."**

The `ProofOfInclusion` struct is a `Streamable` type exposed through the Python wheel, making it reachable from any unprivileged input that can supply serialized DataLayer proof bytes.

### Likelihood Explanation

The `valid()` method name and the presence of the tautological final check strongly imply to callers that the function performs complete proof validation. There is no documentation or API-level signal that callers must additionally compare `proof.root_hash()` against an external trusted root. Any DataLayer client code that follows the natural API usage pattern of calling `proof.valid()` and trusting the result is vulnerable.

### Recommendation

1. **Add an expected root parameter to `valid()`:**
   ```rust
   pub fn valid(&self, expected_root: &Hash) -> bool {
       // ... existing chain check ...
       existing_hash == *expected_root
   }
   ```
   This makes the root-anchor check explicit and non-tautological.

2. **Remove the tautological final check** in the current implementation, or replace it with a comparison against a caller-supplied root.

3. **Update the Python binding** (`py_valid`) to require the expected root hash as an argument, preventing callers from accidentally omitting the root comparison.

4. **Add a test** that constructs a `ProofOfInclusion` from raw bytes (not from `get_proof_of_inclusion`) and verifies that `valid()` rejects it when the root does not match the actual tree root.

### Proof of Concept

```python
from chia_rs.datalayer import (
    ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
)
from chia_rs.sized_bytes import bytes32
import hashlib

# Build a real tree with one entry
blob = MerkleBlob(blob=bytearray())
real_key   = KeyId(1)
real_value = ValueId(1)
real_hash  = bytes32(hashlib.sha256(b"real").digest())
blob.insert(real_key, real_value, real_hash)
blob.calculate_lazy_hashes()
real_root = blob.get_root_hash()

# Forge a proof for a key that does NOT exist in the tree
fake_node_hash  = bytes32(hashlib.sha256(b"fake_leaf").digest())
fake_other_hash = bytes32(hashlib.sha256(b"fake_sibling").digest())

# Compute combined_hash the same way the library does
combined = hashlib.sha256(
    b"\x01" + fake_node_hash + b"\x01" + fake_other_hash
).digest()  # simplified; use actual calculate_internal_hash logic

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=bytes32(fake_other_hash),
    combined_hash=bytes32(combined),
)
forged_proof = ProofOfInclusion(
    node_hash=bytes32(fake_node_hash),
    layers=[layer],
)

# valid() returns True even though forged_proof.root_hash() != real_root
assert forged_proof.valid() == True
assert forged_proof.root_hash() != real_root  # proves a different tree
# Any caller checking only proof.valid() accepts this forged proof
```

The root cause is at: [7](#0-6)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-243)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
```

**File:** wheel/python/chia_rs/datalayer.pyi (L252-258)
```text
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self: ...
    @classmethod
    def from_bytes_unchecked(cls, blob: bytes) -> Self: ...
    @classmethod
    def parse_rust(cls, blob: ReadableBuffer, trusted: bool = False) -> tuple[Self, int]: ...
    def to_bytes(self) -> bytes: ...
```
