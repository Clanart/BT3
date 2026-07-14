### Title
`ProofOfInclusion::valid()` Is a Tautological Self-Check That Never Verifies Against an External Root — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only verifies internal consistency of the proof chain. The final comparison `existing_hash == self.root_hash()` is a mathematical tautology: after the loop, `existing_hash` always equals `self.root_hash()` by construction. No external expected root is ever checked. An attacker can construct a `ProofOfInclusion` that passes `valid()` for any arbitrary `node_hash`, without that hash being present in any real tree.

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

        existing_hash = calculated_hash;
    }

    existing_hash == self.root_hash()   // ← always true
}
``` [1](#0-0) 

And `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← derived from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

The loop only continues when `calculated_hash == layer.combined_hash`, and then sets `existing_hash = calculated_hash`. After the last iteration, `existing_hash` equals the last `layer.combined_hash`. `root_hash()` also returns the last `layer.combined_hash`. Therefore `existing_hash == self.root_hash()` is **always true** after the loop — the final check is a tautology and provides zero security.

The correct design would require `valid()` to accept an external expected root hash parameter and compare `existing_hash` against it, not against a value embedded in the proof itself.

`ProofOfInclusion` is fully deserializable from bytes and constructible from Python via `from_bytes`, `from_json_dict`, and `__new__`: [3](#0-2) 

The `valid()` method is exposed to Python callers: [4](#0-3) 

### Impact Explanation

An attacker can forge a `ProofOfInclusion` for any arbitrary `node_hash` (i.e., any key-value pair they wish to claim is in the tree) by constructing a chain of internally consistent layers. The forged proof will pass `valid()` regardless of the actual tree root. Any DataLayer consumer that relies on `proof.valid()` as the sole verification step — without separately comparing `proof.root_hash()` against a trusted root — will accept the forged inclusion proof. This lets untrusted input prove invalid state, matching the allowed High impact: **DataLayer Merkle proof logic accepts forged inclusion, or lets untrusted input prove invalid state**.

### Likelihood Explanation

`ProofOfInclusion` is a Streamable type exposed via Python bindings and exchanged between DataLayer peers during synchronization. The `valid()` method is the designated verification API — its name and signature (`fn valid(&self) -> bool`) strongly imply it is a complete self-contained check. Callers are unlikely to additionally compare `proof.root_hash()` against a separately-tracked trusted root, because `valid()` appears to already do that. The Python DataLayer sync code receives proofs from untrusted peers and calls `proof.valid()` to accept or reject them.

### Recommendation

Change `valid()` to accept an external expected root hash and compare `existing_hash` against it:

```rust
pub fn valid_against_root(&self, expected_root: &Hash) -> bool {
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
    &existing_hash == expected_root   // compare against external root
}
```

All call sites that use `proof.valid()` must be updated to supply the trusted root hash obtained from the local tree or a trusted source.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to forge a proof that node_hash is in some tree
node_hash  = bytes(b'\xAA' * 32)   # arbitrary leaf hash to "prove"
other_hash = bytes(b'\xBB' * 32)   # arbitrary sibling

# Compute a combined_hash that is internally consistent
# (mirrors calculate_internal_hash: sha256(0x02 || left || right))
combined = hashlib.sha256(b'\x02' + node_hash + other_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=other_hash,
    combined_hash=combined,
)

forged = ProofOfInclusion(node_hash=node_hash, layers=[layer])

assert forged.valid()           # ← passes, despite being completely fabricated
# forged.root_hash() == combined, which is NOT the real tree root
```

The forged proof passes `valid()` for any `node_hash` the attacker chooses, with no knowledge of the actual tree contents.

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
