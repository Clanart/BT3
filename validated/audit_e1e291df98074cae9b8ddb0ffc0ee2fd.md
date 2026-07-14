### Title
`ProofOfInclusion::valid()` Does Not Bind to a Trusted Root — Forged Inclusion Proof Accepted - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only verifies the internal self-consistency of the proof chain. It derives the root it checks against from the proof's own `combined_hash` field — which is fully attacker-controlled. Any caller that relies solely on `valid()` to authenticate a proof received from an untrusted source will accept a forged proof of inclusion for any arbitrary key-value pair against any attacker-chosen tree root, not the committed on-chain DataLayer root.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct deserializable from untrusted bytes and exposed to Python via PyO3 bindings. Its `valid()` method walks the `layers` vector, verifying that each layer's `combined_hash` equals the hash computed from the running hash and `other_hash`. At the end it checks:

```rust
existing_hash == self.root_hash()
```

But `root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself
    } else {
        self.node_hash
    }
}
```

After the loop, `existing_hash` is exactly `last.combined_hash` (because the loop already verified `calculated_hash == layer.combined_hash` and then set `existing_hash = calculated_hash`). The final comparison is therefore a tautology — it is always `true` when the loop completes without returning `false`. No external trusted root is ever consulted. [1](#0-0) 

The struct is fully deserializable from untrusted bytes and exposed to Python: [2](#0-1) [3](#0-2) 

### Impact Explanation

An attacker can craft a `ProofOfInclusion` with an arbitrary `node_hash` (the leaf they want to falsely prove is included) and a chain of `ProofOfInclusionLayer` values that are internally consistent but anchor to an attacker-chosen root — not the committed DataLayer root stored on-chain. Calling `proof.valid()` on this crafted object returns `true`. Any DataLayer client that authenticates a received proof solely via `proof.valid()` — without also asserting `proof.root_hash() == trusted_committed_root` — will accept a forged proof of inclusion, allowing an attacker to prove that any arbitrary key-value pair exists in a DataLayer store when it does not.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, or lets untrusted input prove invalid state.**

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with full Python bindings (`from_bytes`, `from_json_dict`, `valid()`, `root_hash()`). The DataLayer protocol involves peers exchanging proofs. The `valid()` method's name and signature strongly imply it fully validates the proof, making it likely that callers omit the separate `root_hash()` comparison against the committed on-chain root. The fuzz target itself only calls `proof.valid()` without a root check, reflecting the same pattern. [4](#0-3) 

### Recommendation

1. Add a `valid_for_root(&self, trusted_root: &Hash) -> bool` method that checks internal consistency **and** asserts `self.root_hash() == trusted_root`. Deprecate or remove the root-agnostic `valid()` method from the public API.
2. Update the Python binding to expose only the root-bound variant, requiring callers to supply the committed root.
3. Audit all call sites of `proof.valid()` (Rust and Python) to ensure they also compare `proof.root_hash()` against the on-chain committed DataLayer root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer

# Craft a single-layer proof for an arbitrary node_hash
# with an attacker-chosen combined_hash (= the fake root)
import hashlib

fake_leaf   = bytes([0xAA] * 32)
other_hash  = bytes([0xBB] * 32)
# compute a valid combined_hash so the layer passes the internal check
h = hashlib.sha256(b"\x00" * 30 + b"\x00\x02" + fake_leaf + other_hash).digest()
fake_root   = h  # attacker controls this

layer = ProofOfInclusionLayer(
    other_hash_side=1,   # right
    other_hash=other_hash,
    combined_hash=fake_root,
)
proof = ProofOfInclusion(node_hash=fake_leaf, layers=[layer])

assert proof.valid()          # True — passes with no external root check
assert proof.root_hash() == fake_root  # attacker-chosen root, not the committed one
# A caller that only checks proof.valid() accepts this as a valid proof of inclusion
```

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L8-29)
```rust
#[cfg_attr(
    feature = "py-bindings",
    pyclass(get_all, from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
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
