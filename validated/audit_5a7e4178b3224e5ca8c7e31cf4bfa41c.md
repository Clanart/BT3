### Title
`ProofOfInclusion::valid()` Tautological Root-Hash Check Accepts Any Internally-Consistent Forged Proof — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` is the sole public API for verifying a DataLayer Merkle inclusion proof. Its final root-hash comparison is a tautology: it compares `existing_hash` against `self.root_hash()`, but `self.root_hash()` is derived from the proof's own last layer — the same value `existing_hash` was just set to inside the loop. The check always passes when the loop completes without returning `false`. An attacker can therefore craft a `ProofOfInclusion` (via `from_bytes`) with any `node_hash` and any consistent chain of layers, and `valid()` will return `true`, regardless of whether the claimed key-value pair is actually in the tree.

### Finding Description

**Root cause — `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`:**

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← derived from the proof itself
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
        existing_hash = calculated_hash;   // ← set to layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: both sides are last.combined_hash
}
```

Trace:
1. After the loop, `existing_hash` equals the last `calculated_hash`, which the loop already asserted equals `layer.combined_hash` for the last layer.
2. `self.root_hash()` returns `last.combined_hash` — the identical value.
3. The final comparison is `last.combined_hash == last.combined_hash`, which is unconditionally `true`.

`valid()` therefore only verifies that the hash chain is internally self-consistent. It never compares the computed root against any externally trusted tree root.

**Attacker-controlled entry path:**

`ProofOfInclusion` derives `Streamable` and is exposed via Python bindings with `from_bytes` / `from_bytes_unchecked`. Any code that receives a `ProofOfInclusion` over the network (or from any untrusted source) and calls `.valid()` is vulnerable.

**Forged proof construction:**

1. Choose any `node_hash` (e.g., the hash of a key-value pair the attacker wants to falsely prove is in the tree).
2. Choose any sequence of `(other_hash_side, other_hash)` pairs.
3. For each layer, compute `combined_hash = calculate_internal_hash(prev_hash, other_hash_side, other_hash)`.
4. Serialize and submit. `valid()` returns `true`.

The attacker controls both the claimed leaf and the claimed root, making the proof pass for any assertion.

### Impact Explanation

Any DataLayer client that receives a `ProofOfInclusion` from an untrusted peer and calls `.valid()` to gate trust will accept forged proofs. This allows an attacker to assert that any key-value pair is present in any tree root, enabling false state proofs. This directly matches the allowed High impact: **DataLayer Merkle proof logic accepts forged inclusion, letting untrusted input prove invalid state.**

### Likelihood Explanation

`ProofOfInclusion` is a public, serializable, Python-exposed type. The DataLayer is designed for cross-node data exchange where proofs are transmitted between parties. Any consumer that calls `proof.valid()` as the sole verification step — a natural and expected usage given the method name — is exploitable by any unprivileged attacker who can submit bytes.

### Recommendation

`valid()` must accept an externally trusted root hash and compare against it:

```rust
pub fn valid_against_root(&self, trusted_root: &Hash) -> bool {
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
    existing_hash == *trusted_root   // compare against caller-supplied root
}
```

The no-argument `valid()` should either be removed or clearly documented as an internal-consistency-only check that provides no security guarantee without an external root comparison.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side, merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer}};

fn calculate_internal_hash(left: &Hash, side: Side, right: &Hash) -> Hash {
    // same as crate::calculate_internal_hash
    todo!()
}

fn forge_proof() {
    // Step 1: choose any node_hash (attacker-controlled leaf claim)
    let fake_node_hash: Hash = [0xAA; 32];

    // Step 2: build one layer with arbitrary sibling
    let fake_sibling: Hash = [0xBB; 32];
    let combined = calculate_internal_hash(&fake_node_hash, Side::Left, &fake_sibling);

    let proof = ProofOfInclusion {
        node_hash: fake_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Left,
            other_hash: fake_sibling,
            combined_hash: combined,   // self-consistent
        }],
    };

    // valid() returns true — proof.root_hash() == combined == existing_hash after loop
    assert!(proof.valid());
    // proof.root_hash() is attacker-chosen; no external root was ever checked
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
