### Title
`ProofOfInclusion::valid()` Is Self-Referential and Does Not Verify Against a Trusted Root — Forged DataLayer Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only checks the internal self-consistency of the proof chain. Its final comparison is tautological: it compares the hash it just computed against the same field it derived that hash from. No external trusted root is ever consulted. An attacker who constructs any internally-consistent `ProofOfInclusion` — regardless of whether it corresponds to any real tree state — will receive `true` from `valid()`.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct exposed via Python/wasm bindings. Its `valid()` method is the sole API for verifying a proof:

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs:40-58
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

    existing_hash == self.root_hash()   // ← tautology
}
```

`root_hash()` is:

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs:32-38
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same field the loop just verified against
    } else {
        self.node_hash
    }
}
```

After the loop completes without returning `false`, `existing_hash` holds the last `calculated_hash`. The loop only continues when `calculated_hash == layer.combined_hash`, so after the last iteration `existing_hash == last_layer.combined_hash`. `root_hash()` returns `last_layer.combined_hash`. Therefore `existing_hash == self.root_hash()` is **unconditionally true** whenever the loop completes.

The method verifies only that each layer's `combined_hash` is consistent with the hash computed from the previous layer and `other_hash`. It never compares the final hash against any externally-supplied, trusted tree root. A caller who receives a `ProofOfInclusion` from an untrusted source and calls `valid()` gets `true` for any internally-consistent fabricated proof. [1](#0-0) [2](#0-1) 

### Impact Explanation

Any party that receives a `ProofOfInclusion` from an untrusted peer and calls `valid()` as the sole check will accept a forged proof. An attacker can:

1. Fabricate a `node_hash` claiming any key-value pair is present.
2. Build a chain of `ProofOfInclusionLayer` values that are internally consistent (each `combined_hash` equals the hash of the previous layer combined with a chosen `other_hash`).
3. Serialize the struct via `Streamable` and deliver it to a verifier.
4. `valid()` returns `true`; the verifier accepts the forged state.

This lets untrusted input prove invalid DataLayer state, matching the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."* [3](#0-2) [4](#0-3) 

### Likelihood Explanation

`ProofOfInclusion` is a first-class Python-binding type with `from_bytes` / `parse_rust` deserialization entry points. Any DataLayer client that receives a proof over the network and calls `proof.valid()` without separately comparing `proof.root_hash()` against a locally-known trusted root is vulnerable. The `valid()` method's name and signature give no indication that an external root is required; callers are naturally expected to rely on it as a complete check. [5](#0-4) [6](#0-5) 

### Recommendation

`valid()` must accept a trusted root hash as a parameter and compare the final computed hash against it instead of against `self.root_hash()`:

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
    &existing_hash == trusted_root   // compare against externally-supplied root
}
```

`root_hash()` can remain as a convenience accessor, but `valid()` without a trusted root parameter should either be removed or made to panic to prevent misuse. All call sites — including the Python bindings and the fuzz target — should be updated to supply the known tree root. [1](#0-0) 

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};
use chia_protocol::Bytes32;

fn forged_proof_passes_valid() {
    // Attacker-chosen leaf hash (claims this key is in the tree)
    let fake_node_hash = Hash(Bytes32::new([0xAA; 32]));
    // Attacker-chosen sibling hash
    let fake_other_hash = Hash(Bytes32::new([0xBB; 32]));

    // Compute what combined_hash must be for the layer to be "internally consistent"
    let fake_combined = chia_datalayer::calculate_internal_hash(
        &fake_node_hash,
        Side::Right,
        &fake_other_hash,
    );

    let forged = ProofOfInclusion {
        node_hash: fake_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Right,
            other_hash: fake_other_hash,
            combined_hash: fake_combined,   // attacker controls this
        }],
    };

    // valid() returns true even though this proof was never generated
    // from any real MerkleBlob and corresponds to no committed state.
    assert!(forged.valid());   // passes — tautological final check
    // root_hash() returns fake_combined, not any real tree root
}
```

The tautology is confirmed: after the loop, `existing_hash == fake_combined` and `self.root_hash() == fake_combined`, so the final equality holds unconditionally for any internally-consistent fabrication. [1](#0-0) [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-62)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
}
```
