### Title
`ProofOfInclusion::valid()` Does Not Verify Against an External Trusted Root — Forged Inclusion Proofs Always Pass — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle crate only verifies the internal hash-chain consistency of the proof. Its final check is a logical tautology — it compares `existing_hash` against `self.root_hash()`, but both values are derived from the same `layer.combined_hash` field that was already verified in the loop. No external trusted root is ever compared. An attacker can craft any internally-consistent `ProofOfInclusion` — with an arbitrary `node_hash` and arbitrary claimed root — and `valid()` will return `true`.

### Finding Description

`ProofOfInclusion::valid()` is the sole verification method on the struct:

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

        existing_hash = calculated_hash;   // ← set to layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← tautology
}
``` [1](#0-0) 

After the loop, `existing_hash` equals the last `layer.combined_hash` (because the loop already verified `calculated_hash == layer.combined_hash` and then assigned `existing_hash = calculated_hash`). `root_hash()` returns exactly that same field:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value as existing_hash after loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

Therefore `existing_hash == self.root_hash()` is **always `true`** when the loop completes without returning `false`. The function never compares against any externally-supplied trusted root. No such parameter exists on `valid()` or anywhere else on the struct.

The struct is fully constructable from untrusted input: it is `Streamable`, has a Python `__new__` constructor, and is exposed via `from_bytes` / `from_bytes_unchecked`: [3](#0-2) [4](#0-3) 

### Impact Explanation

Any DataLayer verifier that calls `proof.valid()` as its sole check — without separately comparing `proof.root_hash()` against a blockchain-committed trusted root — will accept a completely forged proof. The attacker controls both the claimed leaf hash (`node_hash`) and the claimed tree root (the last `combined_hash` in `layers`). For the degenerate case of an empty `layers` vec, `valid()` returns `true` for any `node_hash` whatsoever, with the claimed root equal to that same `node_hash`. This matches the allowed impact: **DataLayer Merkle proof logic accepts forged inclusion, letting untrusted input prove invalid state.**

### Likelihood Explanation

The `valid()` method is the only verification entry point on `ProofOfInclusion`. Its name implies a complete validity check. There is no `verify(trusted_root: Hash)` alternative. All tests and the fuzz target call `proof.valid()` without a root comparison: [5](#0-4) [6](#0-5) 

Any downstream consumer of the Python or Rust API that follows the same pattern — calling only `proof.valid()` — is vulnerable. The DataLayer use-case involves sending proofs from a data provider to a verifier; if the verifier trusts `valid()` alone, the data provider can forge inclusion of arbitrary key/value pairs.

### Recommendation

1. Add an external trusted root parameter to `valid()`:
   ```rust
   pub fn valid_against_root(&self, trusted_root: &Hash) -> bool { ... }
   ```
   or rename the current method to `is_internally_consistent()` to make its limitation explicit.
2. The final `existing_hash == self.root_hash()` check should be replaced with `existing_hash == *trusted_root`.
3. Deprecate or remove the no-argument `valid()` from the public API, or have it clearly documented as insufficient for security-critical verification.

### Proof of Concept

```rust
use chia_datalayer::{Hash, ProofOfInclusion, Side};
use chia_protocol::Bytes32;

// Forge a proof for an arbitrary leaf hash with no real tree
let fake_leaf = Hash(Bytes32::new([0xde; 32]));
let forged_proof = ProofOfInclusion {
    node_hash: fake_leaf,
    layers: vec![],   // empty — no real tree needed
};

// valid() returns true: existing_hash (fake_leaf) == root_hash() (fake_leaf)
assert!(forged_proof.valid());
// Claimed root is also attacker-controlled:
assert_eq!(forged_proof.root_hash(), fake_leaf);
```

For a multi-layer forgery, the attacker picks any `node_hash`, any `other_hash`, computes `combined_hash = calculate_internal_hash(node_hash, side, other_hash)`, and constructs a `ProofOfInclusionLayer` with those values. `valid()` will accept it because the chain is internally consistent, regardless of whether any real tree with that root exists. [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L115-124)
```rust
            for kv_id in keys_values.keys().copied() {
                let proof_of_inclusion = match merkle_blob.get_proof_of_inclusion(kv_id) {
                    Ok(proof_of_inclusion) => proof_of_inclusion,
                    Err(error) => {
                        open_dot(merkle_blob.to_dot().unwrap().set_note(&error.to_string()));
                        panic!("here");
                    }
                };
                assert!(proof_of_inclusion.valid());
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
