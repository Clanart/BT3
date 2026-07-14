### Title
`ProofOfInclusion::valid()` Final Root-Hash Check Is a Tautology — Forged Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` derives its expected root hash from the proof itself via `self.root_hash()`, which returns `last.combined_hash`. After the loop, `existing_hash` is guaranteed to equal `last.combined_hash` (because the loop would have returned `false` otherwise). The final check `existing_hash == self.root_hash()` is therefore always `true` when the loop completes. An attacker who can supply a `ProofOfInclusion` to any DataLayer client can forge a self-consistent proof for any arbitrary `node_hash` and have `valid()` return `true`, without the proof corresponding to the actual committed tree root.

### Finding Description

`ProofOfInclusion::valid()` is the sole public API for verifying a DataLayer Merkle proof of inclusion. Its implementation is:

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
    existing_hash == self.root_hash()   // ← always true
}
``` [1](#0-0) 

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same value existing_hash holds after the loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` equals the last `calculated_hash`, which equals the last `layer.combined_hash` (the loop would have returned `false` if they differed). `self.root_hash()` also returns the last `layer.combined_hash`. The final comparison is therefore `last.combined_hash == last.combined_hash` — a tautology. The function never validates the computed root against any external, trusted root hash.

`ProofOfInclusion` is a `Streamable` struct fully exposed through Python bindings, constructable from raw bytes or directly from Python: [3](#0-2) 

The struct is also constructable from Python via `ProofOfInclusion(node_hash, layers)` and deserializable via `from_bytes`. Every existing call site — the fuzz target, Rust tests, and Python tests — relies solely on `valid()` without separately checking `proof.root_hash()` against a known trusted root: [4](#0-3) [5](#0-4) [6](#0-5) 

The analog to the Augur bug is direct: Augur's `lookup()` returns a zero address when a key is unregistered, and that default is used without validation. Here, `root_hash()` returns a value derived entirely from the proof itself (not from an external trusted source), and the final check uses that self-referential value — making the validation trivially bypassable.

### Impact Explanation

Any party that receives a `ProofOfInclusion` from an untrusted source and calls `valid()` to verify it will accept a forged proof. An attacker constructs:

1. `node_hash`: the hash of any key-value pair they wish to falsely claim is in the tree.
2. `layers`: a chain of `ProofOfInclusionLayer` values where each `combined_hash` is correctly computed from the previous hash and a chosen `other_hash`. The attacker controls all `other_hash` values and therefore controls the final `combined_hash` (the "root").

`valid()` returns `true`. The caller has no indication the proof does not correspond to the actual on-chain committed root. This lets untrusted input prove invalid DataLayer state — a forged inclusion proof.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

- `ProofOfInclusion` is fully serializable and deserializable, making it trivially receivable from untrusted peers.
- The Python binding exposes `valid()` as the only proof-verification method; no binding enforces a root-hash comparison.
- All existing tests and the fuzz target call only `valid()`, establishing a pattern that callers follow.
- No documentation warns that `valid()` does not check against an external root.
- Constructing a forged proof requires only arithmetic over SHA-256 — no key material or privileged access needed.

### Recommendation

`valid()` must accept an expected root hash as a parameter and compare the computed root against it:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
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

All call sites must be updated to pass the trusted root hash (obtained from the `MerkleBlob` or from the on-chain commitment). The `root_hash()` helper can remain for informational use but must not be used as the validation target inside `valid()`.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
from hashlib import sha256

def internal_hash(left: bytes, right: bytes) -> bytes:
    return sha256(b"\x02" + left + right).digest()

# Attacker wants to "prove" node_hash is in the tree
node_hash = bytes([0xAA] * 32)   # arbitrary hash attacker claims is included
other_hash = bytes([0xBB] * 32)  # attacker-chosen sibling hash

# Compute a valid combined_hash for one layer
combined = internal_hash(other_hash, node_hash)  # Side.Left means other is left

layer = ProofOfInclusionLayer(
    other_hash_side=0,   # Side.Left
    other_hash=other_hash,
    combined_hash=combined,
)

forged_proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

# valid() returns True even though this proof has nothing to do with any real tree
assert forged_proof.valid(), "Forged proof accepted!"
# forged_proof.root_hash() == combined  (attacker-controlled, not the real tree root)
print("Forged proof passed valid():", forged_proof.valid())
```

The forged proof passes `valid()` because the final check `existing_hash == self.root_hash()` compares `combined` against `combined` — always `true`. No real tree, no real root, no real inclusion. [1](#0-0)

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

**File:** tests/test_datalayer.py (L337-339)
```python
        for kv_id in keys_values.keys():
            proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
            assert proof_of_inclusion.valid()
```
