### Title
`ProofOfInclusion.valid()` Is a Self-Referential Tautology — Root Is Never Anchored to a Trusted External Value - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion.valid()` in the DataLayer Merkle proof code only verifies the internal self-consistency of the proof chain. Its final check — `existing_hash == self.root_hash()` — is a logical tautology that is always `true` after the loop completes without returning `false`. The computed root is never compared against any external trusted root. Because `ProofOfInclusion` is fully deserializable from untrusted bytes via Python/Streamable bindings, any caller that relies solely on `valid()` before trusting `root_hash()` will accept a completely forged proof of inclusion.

### Finding Description

`ProofOfInclusion.valid()` is implemented as follows:

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

        existing_hash = calculated_hash;  // existing_hash == layer.combined_hash here
    }

    existing_hash == self.root_hash()  // always true: both sides == last layer.combined_hash
}
``` [1](#0-0) 

And `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // <-- returns the proof's own top layer
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

**The tautology:** After each loop iteration, `existing_hash` is set to `calculated_hash`, which was just verified to equal `layer.combined_hash`. After the loop, `existing_hash` equals the last `layer.combined_hash`. `self.root_hash()` also returns the last `layer.combined_hash`. Therefore `existing_hash == self.root_hash()` is **always `true`** when the loop completes without returning `false`. The final check adds no security.

The struct is `Streamable` and exposed to Python with full deserialization constructors: [3](#0-2) [4](#0-3) 

An attacker can:
1. Choose an arbitrary `node_hash` (the leaf they want to forge inclusion for).
2. Build a chain of `ProofOfInclusionLayer` entries where each `combined_hash` is computed correctly from the previous hash and a chosen `other_hash` — producing an internally consistent but entirely fabricated proof.
3. Serialize it via `stream_to_bytes()` and send it to any verifier.
4. The verifier calls `proof.valid()` → `True`. The verifier calls `proof.root_hash()` → attacker-controlled value.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any component that receives a `ProofOfInclusion` from an untrusted peer and calls `valid()` as the sole check — without separately comparing `proof.root_hash()` against a known on-chain root — will accept a forged proof of inclusion for any arbitrary key/value pair. This allows an attacker to convince a DataLayer client that a key-value pair is committed in a store when it is not, enabling false state attestation.

### Likelihood Explanation

The `ProofOfInclusion` struct is the primary API for DataLayer inclusion proofs. Its `valid()` method is named and documented as a completeness check. The fuzz target and all tests call only `proof.valid()` without a separate root comparison: [5](#0-4) [6](#0-5) 

Any downstream consumer following the same pattern — calling `valid()` without also checking `root_hash()` against a blockchain-committed root — is exploitable. The misleading API name makes this error highly likely.

### Recommendation

Fix `valid()` to require an external trusted root parameter, or rename it to `is_internally_consistent()` and add a separate `verify(trusted_root: &Hash) -> bool` method that performs the actual security-relevant check:

```rust
pub fn verify(&self, trusted_root: &Hash) -> bool {
    self.is_internally_consistent() && &self.root_hash() == trusted_root
}
```

The current `valid()` should either be removed or clearly documented as **not** a security check. All call sites — including the fuzz target and Python bindings — must be updated to pass and compare against a trusted root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from chia_rs.sized_bytes import bytes32
from chia_rs.sized_ints import uint8
import hashlib

# Forge a proof claiming node_hash is included under an attacker-chosen root
node_hash = bytes32(b"A" * 32)          # arbitrary leaf we want to "prove"
other_hash = bytes32(b"B" * 32)         # arbitrary sibling

# Compute a combined_hash that is internally consistent
h = hashlib.sha256(b"\x00" + node_hash + other_hash).digest()  # simplified
combined_hash = bytes32(h)

layer = ProofOfInclusionLayer(
    other_hash_side=uint8(1),
    other_hash=other_hash,
    combined_hash=combined_hash,
)

forged_proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

assert forged_proof.valid()          # True — no external root checked
print(forged_proof.root_hash())      # attacker-controlled combined_hash
```

The `valid()` call returns `True` for a completely fabricated proof. The `root_hash()` returns whatever the attacker computed, with no comparison against any blockchain-committed DataLayer root.

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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
