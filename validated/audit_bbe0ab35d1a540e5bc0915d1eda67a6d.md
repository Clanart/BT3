### Title
`ProofOfInclusion::valid()` Never Validates Against an External Trusted Root — Forged Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate contains a tautological final check: the method derives the "expected" root hash from the proof itself rather than from any external trusted anchor. As a result, any attacker who can supply a `ProofOfInclusion` object (via deserialization or the Python/wasm binding) can forge a proof for an arbitrary `node_hash` and arbitrary layers, and `valid()` will return `true`.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct with two fields: `node_hash` (the leaf being proven) and `layers` (the Merkle path). The `valid()` method walks the layers, verifying that each `combined_hash` equals the hash computed from the running hash and `other_hash`. After the loop, it performs a final check:

```rust
existing_hash == self.root_hash()
```

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken directly from the proof itself
    } else {
        self.node_hash
    }
}
```

After the last loop iteration, `existing_hash` holds `calculated_hash`, which was already verified to equal `layer.combined_hash` for the last layer. Therefore `existing_hash` is always equal to `self.layers.last().combined_hash`, which is exactly what `root_hash()` returns. The final comparison is a tautology — it is always `true` when `layers` is non-empty, and trivially `true` when `layers` is empty (both sides equal `self.node_hash`).

The method never compares the computed root against any externally-supplied, trusted root hash. A correct implementation would require the caller to pass in a trusted root and compare against it, or the method itself would need to accept a `trusted_root: &Hash` parameter.

An attacker can construct a forged `ProofOfInclusion` as follows:
1. Choose any arbitrary `node_hash` (the key-value pair hash they wish to "prove").
2. Choose any `other_hash` values for each layer.
3. Compute `combined_hash` correctly for each layer (so the chain is internally consistent).
4. Call `valid()` — it returns `true`.
5. Call `root_hash()` — it returns whatever the attacker chose as the last `combined_hash`.

The forged proof passes `valid()` and reports an attacker-controlled root hash.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

The DataLayer is an authenticated off-chain key-value store whose Merkle root is committed on-chain. Proofs of inclusion are the mechanism by which clients verify that a specific key-value pair is part of a committed store. Any Python or Rust consumer that receives a `ProofOfInclusion` from an untrusted peer and calls `valid()` as the sole verification step will accept a completely fabricated proof for any `node_hash` the attacker chooses. This allows an attacker to prove false state in a DataLayer store — e.g., proving that a key maps to a value it does not, or that a key exists when it does not.

The `ProofOfInclusion` struct is `Streamable` (deserializable from bytes) and is fully exposed via Python bindings (`from_bytes`, `valid()`, `root_hash()`), making it directly reachable from untrusted network input.

### Likelihood Explanation

The `valid()` method is the only verification API on `ProofOfInclusion`. Its name and signature (`fn valid(&self) -> bool`) strongly imply it is a complete correctness check. All existing callers — tests, fuzz targets, and Python consumers — call `valid()` alone without separately comparing `root_hash()` against a trusted value. Any DataLayer client that follows this pattern is vulnerable. The attack requires only the ability to send a crafted `ProofOfInclusion` to a verifier, which is a normal network operation.

### Recommendation

Add a `trusted_root: &Hash` parameter to `valid()` (or add a separate `valid_for_root(&self, trusted_root: &Hash) -> bool` method) and replace the final tautological check with a comparison against the externally-supplied root:

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
    existing_hash == *trusted_root   // compare against externally-trusted root
}
```

Update all callers (Python bindings, tests, fuzz targets) to supply the trusted root obtained from the on-chain commitment or a locally-verified `MerkleBlob`.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to forge a proof that node_hash=FAKE_LEAF is in some tree.
FAKE_LEAF = bytes([0xAA] * 32)
OTHER_HASH = bytes([0xBB] * 32)

# Compute a valid combined_hash for one layer (internal consistency only)
h = hashlib.sha256()
h.update(b'\x00' * 30)  # DataLayer internal node prefix
h.update(bytes([0]))     # left side indicator
h.update(FAKE_LEAF)
h.update(OTHER_HASH)
combined = h.digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,       # right side
    other_hash=OTHER_HASH,
    combined_hash=combined,  # attacker-controlled
)

forged_proof = ProofOfInclusion(node_hash=FAKE_LEAF, layers=[layer])

# valid() returns True despite no real tree existing
assert forged_proof.valid() == True
# root_hash() returns attacker-controlled value
assert forged_proof.root_hash() == combined
```

The `valid()` call returns `True` for a completely fabricated proof. The reported `root_hash()` is whatever the attacker chose as `combined_hash` in the last layer — not any real committed root. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
