### Title
`ProofOfInclusion.valid()` Does Not Verify Against an External Trusted Root — Forged DataLayer Inclusion Proofs Pass Validation - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks the internal self-consistency of the proof's own fields. It never compares the computed root against any externally-supplied, trusted tree root. Because `root_hash()` returns a value taken directly from the proof itself (`last.combined_hash`), an attacker who can supply a `ProofOfInclusion` object (via Python/wasm bindings or Streamable deserialization) can fabricate a completely self-consistent proof for any arbitrary `node_hash` and have `valid()` return `true`, without the proof being anchored to any real DataLayer tree.

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
    existing_hash == self.root_hash()
}
``` [1](#0-0) 

And `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

The final check `existing_hash == self.root_hash()` is a tautology: `existing_hash` is the hash computed by walking the layers, and `self.root_hash()` returns `last.combined_hash` — the same value that `existing_hash` was just set to in the last iteration. The method never compares against any externally-committed, trusted root. An attacker can construct a `ProofOfInclusion` with arbitrary `node_hash` and a chain of self-consistent layers, and `valid()` will return `true`.

The struct is `Streamable` (deserializable from bytes) and is fully exposed via Python bindings: [3](#0-2) [4](#0-3) 

The Python binding exposes `valid()` and `root_hash()` as separate methods, but `valid()` is the primary verification API and its name implies sufficiency. Callers who receive a `ProofOfInclusion` from an untrusted source and call only `proof.valid()` will accept forged proofs.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any application that:
1. Receives a serialized `ProofOfInclusion` from an untrusted peer/relayer
2. Deserializes it via `ProofOfInclusion::from_bytes()` (Streamable)
3. Calls `proof.valid()` to verify it

…will accept a completely fabricated proof for any `node_hash` the attacker chooses. The attacker can claim any key-value pair is present in a DataLayer tree when it is not.

### Likelihood Explanation

The `valid()` method name strongly implies it is the complete verification check. The Python API exposes it as the primary proof-verification method. Any DataLayer client that verifies proofs received over the network (e.g., from a DataLayer server or peer) and relies solely on `proof.valid()` is vulnerable. The attacker-controlled entry path is direct: craft a `ProofOfInclusion` bytes, send it to a verifier, have `valid()` return `true`.

### Recommendation

`valid()` must accept an externally-supplied trusted root hash parameter and compare the computed root against it:

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

The existing `valid()` (self-referential check only) should be renamed to `internally_consistent()` or deprecated, so callers are not misled into thinking it provides security guarantees without an external root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from chia_rs import calculate_internal_hash  # or equivalent

# Attacker wants to forge a proof that node_hash X is in some tree
fake_node_hash = bytes([0xAA] * 32)
fake_other_hash = bytes([0xBB] * 32)

# Compute a self-consistent combined_hash
fake_combined = calculate_internal_hash(fake_node_hash, 0, fake_other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=0,
    other_hash=fake_other_hash,
    combined_hash=fake_combined,
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True — no real tree was involved
assert forged_proof.valid() == True
# root_hash() returns the attacker-controlled combined_hash
assert forged_proof.root_hash() == fake_combined
```

The `valid()` call returns `True` for a completely fabricated proof because the final comparison `existing_hash == self.root_hash()` reduces to `fake_combined == fake_combined`. [1](#0-0) [5](#0-4)

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
