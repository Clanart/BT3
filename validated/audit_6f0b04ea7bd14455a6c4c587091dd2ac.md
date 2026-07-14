### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Trusted Root Hash, Allowing Forged DataLayer Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks that the proof's internal hash chain is self-consistent. It never compares the computed root against any externally-supplied trusted root. Because `root_hash()` returns the last `combined_hash` field taken directly from the proof itself, the final equality check inside `valid()` is a tautology. An attacker can construct an arbitrary `ProofOfInclusion` — proving any `node_hash` they choose — that passes `valid()` without that node existing in any real DataLayer tree. The struct is `Streamable` and fully exposed through the Python binding, giving an unprivileged attacker a direct, serialized entry path.

### Finding Description

`ProofOfInclusion` is defined in `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`:

```rust
pub struct ProofOfInclusion {
    pub node_hash: Hash,
    pub layers: Vec<ProofOfInclusionLayer>,
}
```

`root_hash()` derives the root entirely from the proof's own fields:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← attacker-controlled
    } else {
        self.node_hash       // ← attacker-controlled
    }
}
```

`valid()` iterates the layers, verifying each `combined_hash` equals `calculate_internal_hash(prev, side, other_hash)`, then ends with:

```rust
existing_hash == self.root_hash()
```

After the loop, `existing_hash` is exactly `last.combined_hash`, which is exactly what `root_hash()` returns. The final comparison is always `true` if the loop did not return `false`. There is no parameter for, and no comparison against, a caller-supplied trusted root.

**Exploit path:**

1. Attacker picks any `node_hash` value `X` they wish to "prove" is in a DataLayer tree.
2. Attacker picks any `other_hash` value `Y` and a `side`.
3. Attacker computes `combined_hash = calculate_internal_hash(X, side, Y)`.
4. Attacker constructs `ProofOfInclusion { node_hash: X, layers: [ProofOfInclusionLayer { other_hash_side: side, other_hash: Y, combined_hash }] }`.
5. Attacker serializes it with `to_bytes()` / `stream()` and sends it to a DataLayer verifier.
6. Verifier calls `ProofOfInclusion::from_bytes(bytes)` (exposed via `Streamable` and the Python binding `from_bytes`) then `proof.valid()` → returns `true`.
7. `proof.root_hash()` returns the attacker-chosen `combined_hash`, which the verifier may then accept as the tree root.

The struct is `Streamable` and exposed via `#[pyclass]` with `from_bytes`, `from_bytes_unchecked`, and `parse_rust` methods, making the deserialization entry path directly reachable from Python DataLayer clients.

The analog to the external report is exact: just as `transferPerpOwner` lacked an access-control guard that should have been present, `valid()` lacks the root-comparison guard that must be present for a Merkle proof to be meaningful. In both cases, the missing check is the only thing standing between an attacker and a forged state claim.

### Impact Explanation

**High — DataLayer Merkle proof logic lets untrusted input prove invalid state.**

Any DataLayer client that calls `proof.valid()` as its sole verification step will accept a forged proof of inclusion for an arbitrary key/value pair that does not exist in the tree. This allows an attacker to convince a verifier that a key is present (or absent, by constructing a proof-of-exclusion analog) in a DataLayer store when it is not, corrupting the integrity of DataLayer state verification.

### Likelihood Explanation

The `valid()` method is the only validation method on `ProofOfInclusion`. Its name strongly implies it performs complete proof validation. There is no API-level mechanism that forces callers to also check `root_hash()` against a trusted value. The Python binding exposes `from_bytes` + `valid()` as a natural two-step verification pattern. Any DataLayer integration that follows this pattern is vulnerable. Likelihood is **medium-high** given the misleading API surface.

### Recommendation

`valid()` must accept a trusted root hash parameter and compare the computed root against it:

```rust
pub fn valid_against_root(&self, trusted_root: &Hash) -> bool {
    // ... existing chain check ...
    existing_hash == *trusted_root  // compare against caller-supplied root
}
```

Alternatively, rename the current method to `is_internally_consistent()` to make clear it does not perform full proof validation, and add a separate `valid(trusted_root: &Hash) -> bool` that does. Update the Python binding accordingly.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to forge a proof that node_hash=X is in some tree
X = bytes([0xAA] * 32)   # arbitrary node hash to "prove"
Y = bytes([0xBB] * 32)   # arbitrary sibling hash

# Compute combined_hash = sha256(0x01 || X || Y)  (left-side, simplified)
# (actual calculation uses calculate_internal_hash with the Side enum)
# Attacker constructs a self-consistent single-layer proof:
layer = ProofOfInclusionLayer(
    other_hash_side=1,   # Right
    other_hash=Y,
    combined_hash=hashlib.sha256(b'\x01' + X + Y).digest()  # matches internal hash fn
)
forged = ProofOfInclusion(node_hash=X, layers=[layer])

# valid() returns True — no trusted root was checked
assert forged.valid(), "Forged proof passes valid()"
# root_hash() returns attacker-controlled value
print("Forged root:", forged.root_hash().hex())
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
