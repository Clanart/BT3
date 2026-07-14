### Title
`ProofOfInclusion::valid()` Does Not Bind Proof to a Trusted External Root — Forged Inclusion Proof Accepted — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only verifies internal chain consistency of the proof layers. It never compares the computed root against a caller-supplied trusted root. Because the final comparison `existing_hash == self.root_hash()` is a tautology (always `true` when the loop completes), an attacker who controls a serialized `ProofOfInclusion` can forge a proof that passes `valid()` while proving inclusion in a completely different tree, not the actual DataLayer tree.

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

        existing_hash = calculated_hash;   // ← always equals layer.combined_hash here
    }

    existing_hash == self.root_hash()      // ← tautology: always true
}
``` [1](#0-0) 

`root_hash()` returns `self.layers.last().combined_hash`:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same value as existing_hash after the loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` is always equal to the last `layer.combined_hash` (because the loop would have returned `false` otherwise). `self.root_hash()` returns that same `last.combined_hash`. Therefore the final guard `existing_hash == self.root_hash()` is always `true` when the loop completes — it is dead code that provides no security.

The method never accepts a trusted external root hash parameter. A caller who receives a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` only learns that the proof's layers are internally self-consistent — not that the proof's root matches any particular DataLayer tree.

`ProofOfInclusion` is a `Streamable` type fully exposed to Python via `from_bytes`, `from_bytes_unchecked`, `from_json_dict`, and direct construction (`__new__`): [3](#0-2) 

The Python binding exposes `valid()` as the sole validation method with no root parameter: [4](#0-3) 

### Impact Explanation

An attacker who can deliver a serialized `ProofOfInclusion` to a verifier (e.g., over the DataLayer peer protocol) can:

1. Choose any arbitrary `node_hash` (claiming any key-value pair is included).
2. Build a chain of internally consistent layers using `calculate_internal_hash` with attacker-chosen `other_hash` values, producing a self-consistent `combined_hash` at each level.
3. The resulting proof passes `proof.valid() == True` even though its `root_hash()` has nothing to do with the actual DataLayer tree root.

If the verifier does not separately check `proof.root_hash() == trusted_root` (which the API does not require or document), the attacker proves inclusion of an arbitrary key-value pair in an arbitrary tree. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic lets untrusted input prove invalid state**.

By contrast, the consensus-layer `validate_merkle_proof` in `chia-consensus` correctly binds the proof to an external root before accepting it: [5](#0-4) 

The DataLayer `ProofOfInclusion::valid()` has no equivalent binding.

### Likelihood Explanation

- `ProofOfInclusion` is a `Streamable` type deserializable from arbitrary bytes, making it directly reachable from untrusted network input.
- The method name `valid()` strongly implies complete proof validation; callers are unlikely to add a separate root check.
- No documentation warns that `valid()` does not check against a trusted root.
- The Python binding exposes `valid()` as the only validation entry point, with no root parameter.

### Recommendation

Add a `trusted_root` parameter to `valid()` (or add a separate `valid_with_root(trusted_root: &Hash) -> bool` method) that compares the computed root against the caller-supplied trusted root:

```rust
pub fn valid_with_root(&self, trusted_root: &Hash) -> bool {
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
    &existing_hash == trusted_root   // bind to external trusted root
}
```

Expose this as the primary Python API and deprecate the root-free `valid()`. All call sites that use `proof.valid()` should be updated to supply the trusted DataLayer tree root obtained from a trusted source (e.g., the on-chain committed root hash).

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from chia_rs import calculate_internal_hash  # or equivalent

# Attacker-chosen leaf hash (claims this key is in the tree)
fake_node_hash = bytes([0xAA] * 32)
# Attacker-chosen sibling hash
fake_other_hash = bytes([0xBB] * 32)
# Attacker computes a valid combined_hash using the real hash function
fake_combined = calculate_internal_hash(fake_node_hash, 0, fake_other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=0,
    other_hash=fake_other_hash,
    combined_hash=fake_combined,
)
forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# Passes valid() even though it proves inclusion in an attacker-controlled tree
assert forged_proof.valid() == True
# root_hash() is attacker-controlled, not the real DataLayer root
assert forged_proof.root_hash() == fake_combined
```

The forged proof passes `valid()` with no knowledge of the actual DataLayer tree, because `valid()` never checks against a trusted external root. [1](#0-0) [6](#0-5)

### Citations

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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-266)
```text
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

**File:** crates/chia-consensus/src/merkle_tree.rs (L334-344)
```rust
pub fn validate_merkle_proof(
    proof: &[u8],
    item: &[u8; 32],
    root: &[u8; 32],
) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root {
        return Err(SetError);
    }
    Ok(tree.generate_proof(item)?.0)
}
```
