### Title
`ProofOfInclusion::valid()` Accepts Forged Inclusion Proofs Without External Root Verification — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle implementation only verifies the internal hash-chain consistency of the proof object itself. It never checks the computed root against any external, trusted tree root. Because the `root_hash()` it compares against is derived from the proof's own last `combined_hash` field, the final equality check is tautologically true whenever the loop passes. An attacker can craft a fully self-consistent but entirely fabricated `ProofOfInclusion` — claiming any key is included in any tree — and `valid()` will return `true`.

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
    existing_hash == self.root_hash()   // ← always true when loop passes
}
``` [1](#0-0) 

And `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` equals the last `layer.combined_hash` (because the loop would have returned `false` otherwise). `self.root_hash()` also returns `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is always `true` when the loop completes — the final check is a tautology. The method never compares against any externally supplied, trusted root.

`ProofOfInclusion` is a `Streamable` type exposed directly to Python via `py_valid()`: [3](#0-2) 

It is also fully constructible from arbitrary bytes via `from_bytes` / `from_bytes_unchecked`: [4](#0-3) 

The sibling's dirty flag is also not checked during proof generation, meaning a proof built from a tree with uncommitted (dirty) ancestor hashes will embed stale sibling hashes: [5](#0-4) 

### Impact Explanation

Any Python or Rust caller that receives a `ProofOfInclusion` from an untrusted source (e.g., a DataLayer peer) and calls `proof.valid()` to decide whether a key-value pair is included in a committed tree root will accept a completely fabricated proof. The attacker can:

1. Choose any `node_hash` (claiming any key is present).
2. Build a chain of `ProofOfInclusionLayer` values where each `combined_hash` equals `calculate_internal_hash(prev, side, other_hash)` for arbitrary `other_hash` values.
3. The resulting `ProofOfInclusion` passes `valid()` unconditionally.

This allows an untrusted DataLayer peer to prove false state — claiming a key-value pair is included in a tree when it is not — satisfying the allowed impact: **DataLayer Merkle proof logic accepts forged inclusion, letting untrusted input prove invalid state**.

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with a Python binding. DataLayer clients exchange proofs over the network. The method is named `valid()`, strongly implying it is a complete validity check. The fuzz target and all tests call only `proof.valid()` without separately verifying `proof.root_hash()` against a known committed root: [6](#0-5) 

Any DataLayer application that follows the same pattern — calling `proof.valid()` as the sole check — is exploitable by any peer that sends a crafted proof.

### Recommendation

`valid()` must accept an external trusted root parameter and compare against it:

```rust
pub fn valid_against_root(&self, trusted_root: &Hash) -> bool {
    // existing chain check ...
    existing_hash == *trusted_root   // compare against external root, not self.root_hash()
}
```

Alternatively, rename the current method to `is_internally_consistent()` and add a separate `valid(trusted_root: &Hash)` that performs the external root check. Update all callers — including the Python binding and the fuzz target — to supply the trusted root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from hashlib import sha256

# Attacker fabricates a proof claiming node_hash is included
fake_node_hash = bytes([0xAA] * 32)
fake_other_hash = bytes([0xBB] * 32)

# Build a consistent combined_hash: internal_hash(fake_node_hash, fake_other_hash)
combined = sha256(b"\x02" + fake_node_hash + fake_other_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Side.Right
    other_hash=fake_other_hash,
    combined_hash=combined,
)
proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True for a completely fabricated proof
assert proof.valid(), "forged proof accepted"
# root_hash() returns the attacker-chosen combined value, not any real tree root
print("forged root:", proof.root_hash().hex())
```

The `valid()` call succeeds because `calculate_internal_hash(fake_node_hash, Right, fake_other_hash) == combined` and `root_hash()` returns `combined` from the proof itself — no external tree is consulted. [1](#0-0) [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-61)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1180-1187)
```rust
            let sibling_index = parent.sibling_index(index)?;
            let sibling_block = self.get_block(sibling_index)?;
            let sibling = sibling_block.node;
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
