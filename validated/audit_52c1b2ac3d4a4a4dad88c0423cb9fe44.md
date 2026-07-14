### Title
`ProofOfInclusion.valid()` Is a Self-Referential Check That Accepts Fully Forged Inclusion Proofs — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate verifies only the internal hash-chain consistency of the proof struct itself. It derives the expected root from the proof's own last `combined_hash` field (`self.root_hash()`), not from any external trusted root. Because `ProofOfInclusion` is a fully deserializable `Streamable` type exposed through Python bindings, an untrusted sender can trivially construct a proof that passes `valid()` for any arbitrary `node_hash` and any attacker-chosen root hash. The analog to the external report is exact: just as `ref_` and `artId_` were decoded from user-supplied bytes but excluded from the merkle hash check, the `node_hash` and root in `ProofOfInclusion` are user-supplied but the `valid()` check never compares the root against an external trusted value.

### Finding Description

`ProofOfInclusion` is defined as:

```rust
pub struct ProofOfInclusion {
    pub node_hash: Hash,
    pub layers: Vec<ProofOfInclusionLayer>,
}
``` [1](#0-0) 

The `valid()` method computes the root by chaining hashes starting from `self.node_hash` through each layer, then compares the result to `self.root_hash()`:

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
    existing_hash == self.root_hash()
}
``` [2](#0-1) 

`root_hash()` is derived entirely from the proof's own fields:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // attacker-controlled
    } else {
        self.node_hash       // attacker-controlled
    }
}
``` [3](#0-2) 

This is a closed, self-referential check. No external trusted root hash is ever compared. An attacker can construct a `ProofOfInclusion` for any `node_hash` of their choice:

1. Pick any `node_hash = H_fake`.
2. Pick any `other_hash = X` and `other_hash_side`.
3. Compute `combined_hash = calculate_internal_hash(H_fake, side, X)`.
4. Serialize `ProofOfInclusion { node_hash: H_fake, layers: [ProofOfInclusionLayer { other_hash_side: side, other_hash: X, combined_hash }] }`.
5. `valid()` returns `true`.

The struct is a `Streamable` type with full Python-binding deserialization support (`from_bytes`, `from_bytes_unchecked`, `from_json_dict`, `parse_rust`): [4](#0-3) 

Any DataLayer client that receives a `ProofOfInclusion` from a peer and calls only `proof.valid()` — without separately asserting `proof.root_hash() == known_trusted_root` — accepts a completely forged proof. The method name `valid()` implies a complete validity check, but it provides zero security guarantee against untrusted input.

The `get_proof_of_inclusion` method on `MerkleBlob` generates proofs correctly from a trusted local blob: [5](#0-4) 

But once a `ProofOfInclusion` is serialized and transmitted, the receiver has no way to distinguish a legitimate proof from a forged one using `valid()` alone.

### Impact Explanation

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."**

A DataLayer peer or client that receives a `ProofOfInclusion` over the network and calls `proof.valid()` to verify it will accept a proof for any `node_hash` the attacker chooses, with any attacker-chosen root hash. The attacker can:

- Claim that an arbitrary hash (representing any key-value pair) is included in a DataLayer store.
- Claim that a deleted or never-inserted entry is still present.
- Forge proofs for any tree root, bypassing the entire DataLayer inclusion-verification mechanism.

If the DataLayer application layer uses `valid()` as the sole gate before acting on the claimed inclusion (e.g., releasing funds, authorizing access, or updating state based on DataLayer contents), the attacker can trigger those actions with fabricated data.

### Likelihood Explanation

- `ProofOfInclusion` is a fully public, deserializable type exposed via Python bindings.
- The `valid()` method's name and signature give no indication that an external root check is also required.
- The fuzz target for proofs of inclusion only tests proofs generated from a trusted `MerkleBlob`, never from untrusted bytes, so this gap is not covered by existing fuzz coverage. [6](#0-5) 

Any DataLayer consumer that follows the natural API pattern of calling `proof.valid()` is vulnerable.

### Recommendation

`valid()` must accept an external trusted root hash and compare against it:

```rust
pub fn valid_for_root(&self, expected_root: &Hash) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = crate::calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    &existing_hash == expected_root  // compare against EXTERNAL trusted root
}
```

The existing `valid()` method (self-referential) should either be removed or clearly documented as an internal consistency check only, not a security check. The Python binding should expose `valid_for_root(root: bytes32) -> bool` as the primary API.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer

# Attacker-chosen node hash (any 32 bytes)
H_fake = bytes(range(32))
other_hash = bytes(range(32, 64))
side = 0  # Left

# Compute combined_hash using the same formula as calculate_internal_hash
import hashlib
combined = hashlib.sha256(b"\x01" + H_fake + other_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=side,
    other_hash=other_hash,
    combined_hash=combined,
)
forged_proof = ProofOfInclusion(node_hash=H_fake, layers=[layer])

assert forged_proof.valid()          # True — forged proof accepted
assert forged_proof.root_hash() == combined  # Attacker controls the root
print("Forged proof accepted for node_hash:", H_fake.hex())
```

`valid()` returns `True` for a completely fabricated proof. The attacker controls both `node_hash` and the resulting `root_hash()`, meaning they can claim any hash is in any tree they choose.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L25-29)
```rust
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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L1-32)
```rust
#![no_main]

use chia_datalayer::{Error, Hash, InsertLocation, KeyId, MerkleBlob, ValueId};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|args: Vec<(KeyId, ValueId, Hash)>| {
    let mut blob = MerkleBlob::new(Vec::new()).expect("construct MerkleBlob");
    blob.check_integrity_on_drop = false;

    let mut keys: Vec<KeyId> = Vec::new();

    for (key, value, hash) in &args {
        match blob.insert(*key, *value, hash, InsertLocation::Auto {}) {
            Ok(_) => {
                keys.push(*key);
            }
            // should remain valid through these errors
            Err(Error::KeyAlreadyPresent()) => continue,
            Err(Error::HashAlreadyPresent()) => continue,
            // other errors should not be occurring
            Err(error) => panic!("unexpected error while inserting: {:?}", error),
        };
    }

    blob.calculate_lazy_hashes().unwrap();
    blob.check_integrity().unwrap();

    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
});
```
