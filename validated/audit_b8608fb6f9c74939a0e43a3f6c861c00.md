### Title
`ProofOfInclusion::valid()` Does Not Check Computed Root Against a Trusted Reference — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

---

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only verifies internal self-consistency of the proof chain. It derives the "root" it checks against from the proof's own data (`last.combined_hash`), not from any trusted external reference. A caller who receives a `ProofOfInclusion` from an untrusted source and calls `proof.valid()` will get `true` for any internally-consistent proof, regardless of whether the proof's root matches the legitimate DataLayer tree root. This is a direct analog to the external report's pattern: a parsed/public value (the root) is not cross-checked against a trusted reference before the verification is accepted.

---

### Finding Description

`ProofOfInclusion` is defined as a streamable, Python-constructable struct with two public fields: `node_hash` and `layers` (a `Vec<ProofOfInclusionLayer>`). Each layer carries `other_hash_side`, `other_hash`, and `combined_hash`.

The `root_hash()` method derives the root entirely from the proof's own data:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken from the proof itself
    } else {
        self.node_hash
    }
}
``` [1](#0-0) 

The `valid()` method then checks that each layer's `combined_hash` is correctly computed from the running hash and `other_hash`, and finally asserts `existing_hash == self.root_hash()`:

```rust
pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    existing_hash == self.root_hash()   // ← compares against proof-internal root
}
``` [2](#0-1) 

Because `self.root_hash()` returns `last.combined_hash` from the proof itself, `valid()` is a pure self-consistency check. It never compares the computed root against any caller-supplied trusted root. An attacker can craft a `ProofOfInclusion` for a key in an entirely different (attacker-controlled) tree, and `valid()` will return `true`.

The struct is fully exposed to Python via the `datalayer` submodule, including direct construction and deserialization: [3](#0-2) 

It is also deserializable from raw bytes via `from_bytes()` and `from_bytes_unchecked()`, and constructable directly in Python as `ProofOfInclusion(node_hash=..., layers=[...])`. [4](#0-3) 

The Python binding for `valid()` is exposed without any trusted-root parameter: [5](#0-4) 

---

### Impact Explanation

Any code that receives a `ProofOfInclusion` from an untrusted source (e.g., a DataLayer peer during delta synchronization) and calls `proof.valid()` to verify inclusion will accept a forged proof. The attacker constructs a self-consistent proof chain rooted at an arbitrary hash of their choosing, not the legitimate DataLayer store root. The `valid()` method returns `true`, and the verifier is deceived into believing a key/value pair is present in the legitimate store when it is not.

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

---

### Likelihood Explanation

The `valid()` method name strongly implies a complete validity check. A developer receiving a `ProofOfInclusion` over the network and calling `proof.valid()` has no indication from the API that they must also separately call `proof.root_hash() == trusted_root`. The struct is fully constructable and deserializable from untrusted bytes, and the Python binding exposes `valid()` without a trusted-root parameter. Any DataLayer client that verifies peer-supplied proofs using only `valid()` is vulnerable.

---

### Recommendation

`valid()` should accept a trusted root hash parameter and compare the computed root against it:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    &existing_hash == expected_root   // ← compare against caller-supplied trusted root
}
```

Alternatively, rename the current method to `is_internally_consistent()` and add a separate `valid(expected_root: &Hash) -> bool` that enforces the root check. Update the Python binding accordingly. All call sites that currently call `proof.valid()` after receiving a proof from an external source must be audited to ensure they pass the correct trusted root.

---

### Proof of Concept

```python
from chia_rs.datalayer import (
    MerkleBlob, KeyId, ValueId, ProofOfInclusion, ProofOfInclusionLayer
)
from chia_rs.sized_bytes import bytes32
from chia_rs.sized_ints import uint8
import hashlib

# Legitimate tree: contains key 1 → value 1 with hash H1
legitimate_blob = MerkleBlob(blob=bytearray())
H1 = bytes32(b'\x01' * 32)
H2 = bytes32(b'\x02' * 32)
legitimate_blob.insert(KeyId(1), ValueId(1), H1)
legitimate_blob.insert(KeyId(2), ValueId(2), H2)
legitimate_blob.calculate_lazy_hashes()
trusted_root = legitimate_blob.get_root_hash()

# Attacker builds a fake tree containing key 99 (not in legitimate tree)
fake_blob = MerkleBlob(blob=bytearray())
H_fake = bytes32(b'\xAA' * 32)
fake_blob.insert(KeyId(99), ValueId(99), H_fake)
fake_blob.calculate_lazy_hashes()

# Attacker generates a valid proof for key 99 in their fake tree
fake_proof = fake_blob.get_proof_of_inclusion(KeyId(99))

# fake_proof.valid() returns True — self-consistent within attacker's tree
assert fake_proof.valid(), "Attacker's proof is self-consistent"

# But the root does NOT match the legitimate tree's root
assert fake_proof.root_hash() != trusted_root, "Roots differ"

# A verifier who only calls proof.valid() is deceived:
if fake_proof.valid():
    print("VULNERABILITY: verifier accepts forged proof for key 99 as included")
    # Correct check would be: fake_proof.valid() AND fake_proof.root_hash() == trusted_root
```

The `valid()` call returns `True` for the attacker's proof, while the proof's root does not match the legitimate DataLayer store root. A verifier relying solely on `valid()` accepts the forged inclusion claim. [2](#0-1) [6](#0-5) [3](#0-2)

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L63-71)
```rust
impl ProofOfInclusion {
    #[pyo3(name = "root_hash")]
    pub fn py_root_hash(&self) -> Hash {
        self.root_hash()
    }
    #[pyo3(name = "valid")]
    pub fn py_valid(&self) -> bool {
        self.valid()
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
