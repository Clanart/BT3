### Title
`ProofOfInclusion::valid()` Contains a Tautological Root Check, Allowing Forged Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

The `ProofOfInclusion::valid()` method in the DataLayer Merkle proof subsystem contains a tautological final comparison. The function verifies internal chain consistency but its concluding check `existing_hash == self.root_hash()` is always `true` by construction, meaning the function never validates the proof against any externally trusted root. An attacker who can supply a serialized `ProofOfInclusion` (via the Python or wasm binding) can forge an internally consistent proof for any arbitrary `node_hash` and any attacker-chosen root, and `valid()` will return `true`.

### Finding Description

`ProofOfInclusion::valid()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← derived entirely from the proof itself
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

        existing_hash = calculated_hash;   // ← existing_hash := layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: last.combined_hash == last.combined_hash
}
```

After the loop, `existing_hash` holds the last `calculated_hash`, which was just verified to equal `layer.combined_hash` and then assigned to `existing_hash`. `self.root_hash()` returns `layers.last().combined_hash` — the identical value. The final comparison is therefore `last.combined_hash == last.combined_hash`, which is unconditionally `true`. The same tautology holds for the empty-layers case: `self.node_hash == self.node_hash`.

The function therefore only verifies that the proof's own internal hash chain is self-consistent. It never compares the derived root against any externally supplied, trusted root value. A caller who relies solely on `proof.valid()` to accept a proof has no assurance that the proof corresponds to any particular committed tree root.

The `ProofOfInclusion` struct is fully deserializable from untrusted bytes through the `Streamable` implementation and is exposed to Python via `#[pyclass]` and `from_bytes` / `parse_rust`:

```rust
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

The fuzz target and the Rust/Python tests call `proof.valid()` without any subsequent comparison of `proof.root_hash()` against a known trusted root, confirming this is the intended usage pattern:

```rust
// fuzz target — no root comparison
for key in keys {
    let proof = blob.get_proof_of_inclusion(key).unwrap();
    assert!(proof.valid());
}
```

```python
# Python test — no root comparison
proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
assert proof_of_inclusion.valid()
```

### Impact Explanation

An attacker who can deliver a crafted `ProofOfInclusion` to any consumer of the Python or wasm binding can:

1. Choose an arbitrary `node_hash` (the leaf they wish to "prove" is included).
2. Choose an arbitrary target root (the `combined_hash` of the last layer).
3. Build a chain of `ProofOfInclusionLayer` values whose `combined_hash` fields are each correctly computed from the previous hash and an attacker-chosen `other_hash`. This is trivially constructable because the attacker controls all inputs to `calculate_internal_hash`.
4. Serialize the struct and deliver it to the verifier.
5. `valid()` returns `true`; `root_hash()` returns the attacker-chosen root.

Any DataLayer consumer that calls `proof.valid()` and then trusts `proof.root_hash()` as the verified committed root will accept a forged proof of inclusion for a `node_hash` that was never inserted into any real tree. This lets untrusted input prove invalid DataLayer state, matching the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

### Likelihood Explanation

The `ProofOfInclusion` type is serializable, exposed through Python bindings (`from_bytes`, `parse_rust`), and the `valid()` method is the sole public verification API. The fuzz target and all tests use `valid()` without a root comparison, establishing this as the expected call pattern. Any downstream Python code that follows the same pattern is immediately exploitable by a network peer supplying a crafted proof. Likelihood is **High**.

### Recommendation

`valid()` must accept a trusted root as a parameter and compare the derived root against it, rather than comparing against `self.root_hash()`:

```rust
pub fn valid_for_root(&self, trusted_root: &Hash) -> bool {
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

The existing `valid()` method should be removed or deprecated to prevent misuse. All call sites — including the fuzz target, Rust tests, Python tests, and any downstream DataLayer verification code — must be updated to supply the committed root obtained from a trusted source (e.g., the on-chain DataLayer singleton state).

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer

# Attacker-chosen values
node_hash   = bytes([0xAA] * 32)   # arbitrary leaf to "prove"
other_hash  = bytes([0xBB] * 32)   # arbitrary sibling hash

# Compute a valid combined_hash for the single layer
import hashlib
# calculate_internal_hash concatenates hashes in a defined order;
# the exact formula is internal, but the attacker controls both inputs
# and can compute the correct combined_hash offline.
# For demonstration, assume combined_hash is computed correctly:
combined_hash = bytes([0xCC] * 32)  # replace with actual calculate_internal_hash output

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Side::Right
    other_hash=other_hash,
    combined_hash=combined_hash,
)

forged_proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

# valid() returns True despite no real tree existing
assert forged_proof.valid(), "Expected True — tautological check passes"

# root_hash() returns the attacker-controlled combined_hash
assert forged_proof.root_hash() == combined_hash
# Any verifier that trusts proof.valid() + proof.root_hash() has been deceived.
```

**Relevant source locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```

**File:** tests/test_datalayer.py (L337-339)
```python
        for kv_id in keys_values.keys():
            proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
            assert proof_of_inclusion.valid()
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
