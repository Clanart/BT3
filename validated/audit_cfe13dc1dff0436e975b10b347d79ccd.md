### Title
`ProofOfInclusion::valid()` Validates Against a Self-Derived Root, Not an External Trusted Root — Forged Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate derives the "trusted" root hash from the proof object itself (`self.root_hash()` returns `last.combined_hash`, a field inside the proof), then validates the proof against that self-derived value. The final equality check is therefore tautologically true whenever the internal hash chain is consistent. An attacker can construct a `ProofOfInclusion` for any arbitrary `node_hash` against any fabricated root, and `valid()` will return `true`. This is the direct analog of the external report's pattern: a validation function checks a user-controlled value against itself rather than against a stored canonical value.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct with two public fields: `node_hash: Hash` and `layers: Vec<ProofOfInclusionLayer>`. Each `ProofOfInclusionLayer` contains `other_hash_side`, `other_hash`, and `combined_hash`. [1](#0-0) 

`root_hash()` returns the `combined_hash` of the **last layer** — a field that is part of the proof itself: [2](#0-1) 

`valid()` walks the layer chain, verifying at each step that `calculate_internal_hash(existing_hash, side, other_hash) == layer.combined_hash`, then advances `existing_hash = calculated_hash`. After the loop, it checks `existing_hash == self.root_hash()`: [3](#0-2) 

**The final check is tautological.** After the loop passes, `existing_hash` equals the last `calculated_hash`, which the loop already verified equals `last.combined_hash`. And `self.root_hash()` returns `last.combined_hash`. So the final comparison is always `last.combined_hash == last.combined_hash` — unconditionally `true`.

For the empty-layers case, `existing_hash = self.node_hash` and `root_hash()` returns `self.node_hash`, so it is also always `true`.

**Forge recipe (single-layer proof for any leaf):**
1. Choose any `node_hash` (the leaf to "prove").
2. Choose any `other_hash` and `other_hash_side`.
3. Compute `combined_hash = calculate_internal_hash(node_hash, other_hash_side, other_hash)`.
4. Construct `ProofOfInclusion { node_hash, layers: [ProofOfInclusionLayer { other_hash_side, other_hash, combined_hash }] }`.
5. Call `proof.valid()` → returns `true`. Call `proof.root_hash()` → returns the attacker-chosen `combined_hash`.

The struct is `Streamable` and fully constructible from bytes via `from_bytes()` or directly via `__new__` in Python: [4](#0-3) 

The Python binding `py_valid()` is directly callable on any deserialized or constructed `ProofOfInclusion`: [5](#0-4) 

### Impact Explanation

Any DataLayer consumer that calls `proof.valid()` to decide whether a key-value pair is included in a specific Merkle tree root receives no actual security guarantee. An attacker who can supply a `ProofOfInclusion` object (e.g., over the network, via a serialized blob, or via the Python API) can prove inclusion of **any** `node_hash` in **any** fabricated root. This lets untrusted input prove invalid DataLayer state — matching the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type exposed via Python bindings with `from_bytes()`, `from_json_dict()`, and direct construction. Any code path that receives a `ProofOfInclusion` from an external source and calls `valid()` as the sole check is immediately exploitable. The misleading API name (`valid()` implying full validation) makes it likely that callers omit the separate `root_hash()` comparison against a trusted root.

### Recommendation

`valid()` must accept an external trusted root hash parameter and validate against it, rather than deriving the root from the proof itself:

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
    &existing_hash == trusted_root  // compare against EXTERNAL trusted root
}
```

The existing `valid()` (no-argument form) should be removed or deprecated, as it provides no security guarantee.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to "prove" inclusion of an arbitrary node_hash
node_hash = bytes([0xAA] * 32)
other_hash = bytes([0xBB] * 32)
other_hash_side = 0  # Left

# Compute combined_hash to make the chain internally consistent
# (mirrors calculate_internal_hash logic)
h = hashlib.sha256(b"\x01" + other_hash + node_hash).digest()
combined_hash = bytes(h)

layer = ProofOfInclusionLayer(
    other_hash_side=other_hash_side,
    other_hash=other_hash,
    combined_hash=combined_hash,
)
proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

assert proof.valid()          # True — forged proof passes
assert proof.root_hash() == combined_hash  # Attacker-controlled root
```

The `valid()` call returns `True` for a completely fabricated proof, with no connection to any real `MerkleBlob` tree. [3](#0-2) [2](#0-1) [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L26-38)
```rust
pub struct ProofOfInclusion {
    pub node_hash: Hash,
    pub layers: Vec<ProofOfInclusionLayer>,
}

impl ProofOfInclusion {
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
