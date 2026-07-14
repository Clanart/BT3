### Title
`ProofOfInclusion::valid()` Is Self-Referential and Never Validates Against an External Root Hash — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate performs only internal self-consistency checks. Its final comparison is tautologically true after the loop, meaning the function never verifies the proof's claimed root against any externally-known tree root. An attacker who can supply a crafted `ProofOfInclusion` (e.g., via the `Streamable` deserialization path or Python bindings) can forge a proof of inclusion for any key-value pair and have `valid()` return `true`.

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

    existing_hash == self.root_hash()   // ← always true
}
``` [1](#0-0) 

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

**The tautology:** Inside the loop, every iteration already asserts `calculated_hash == layer.combined_hash` (returning `false` otherwise) and then sets `existing_hash = calculated_hash`. After the last iteration, `existing_hash` equals the last `layer.combined_hash`. `self.root_hash()` also returns the last `layer.combined_hash`. Therefore the final check `existing_hash == self.root_hash()` reduces to `last.combined_hash == last.combined_hash`, which is unconditionally `true` whenever the loop completes.

The function validates only that each layer's stored `combined_hash` is consistent with the hash computed from the previous layer and the sibling. It never compares the final accumulated hash against any externally-trusted tree root. The "root" it checks against is the proof's own last `combined_hash` — attacker-controlled data.

`ProofOfInclusion` is a `Streamable` type exposed through Python bindings: [3](#0-2) [4](#0-3) 

This means any caller who deserializes a `ProofOfInclusion` from an untrusted source and calls `valid()` — without separately comparing `proof.root_hash()` against a known tree root — will accept a forged proof.

### Impact Explanation

An attacker who can deliver a crafted `ProofOfInclusion` blob to a DataLayer client can:

1. Claim any arbitrary `node_hash` (asserting any key-value pair is in the tree).
2. Construct any number of internally consistent layers (each `combined_hash` = `calculate_internal_hash(prev, side, other_hash)` for attacker-chosen `other_hash` and `side`).
3. Receive `valid() == true` from the verifier.

The verifier has no way to distinguish this forged proof from a legitimate one using `valid()` alone. This allows untrusted input to prove invalid DataLayer state, matching the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion or lets untrusted input prove invalid state.**

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` struct with Python bindings, making it a natural wire-format object. DataLayer synchronization involves peers exchanging proofs. Any DataLayer consumer that calls `proof.valid()` as its sole verification step — a natural and expected usage given the method name — is vulnerable. The construction of a valid-looking forged proof requires only arithmetic over SHA-256, with no secret knowledge needed.

### Recommendation

`valid()` must accept an external root hash and compare the final accumulated hash against it:

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
    existing_hash == *expected_root   // compare against caller-supplied root
}
```

The current `valid()` (no-argument form) should either be removed or made to panic, since it provides a false sense of security. All call sites that receive proofs from external sources must be updated to supply the known tree root.

### Proof of Concept

```rust
use chia_datalayer::{Hash, KeyId, MerkleBlob, ValueId, InsertLocation};
use chia_datalayer::merkle::blob::{calculate_internal_hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

fn forge_proof() {
    // Attacker wants to claim key KeyId(999) is in the tree with hash FAKE_LEAF
    let fake_leaf_hash = Hash([0xAA; 32]);
    let fake_sibling   = Hash([0xBB; 32]);

    // Build one internally-consistent layer: combined = H(fake_leaf, Right, fake_sibling)
    let combined = calculate_internal_hash(&fake_leaf_hash, Side::Right, &fake_sibling);

    let forged = ProofOfInclusion {
        node_hash: fake_leaf_hash,
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Right,
            other_hash: fake_sibling,
            combined_hash: combined,   // attacker sets this to whatever they computed
        }],
    };

    // valid() returns true — no external root is checked
    assert!(forged.valid());
    // forged.root_hash() == combined, which is NOT the real tree root,
    // but valid() never compared against the real tree root.
}
``` [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-29)
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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-244)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...

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
