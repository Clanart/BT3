### Title
`ProofOfInclusion.valid()` Does Not Validate Against an Expected Root Hash — Forged DataLayer Inclusion Proofs Accepted - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion.valid()` only checks the internal self-consistency of the proof's hash chain. It derives the root hash from the proof's own data (`self.layers.last().combined_hash`) rather than accepting an externally-supplied expected root. Because `ProofOfInclusion` is a `Streamable` type that can be deserialized from untrusted bytes, an attacker can craft a proof that is internally consistent but corresponds to a completely different tree, and `valid()` will return `true`.

### Finding Description

`ProofOfInclusion` is defined as a `Streamable` struct, meaning it can be serialized and deserialized from raw bytes across the network boundary. [1](#0-0) 

The `valid()` method verifies that each layer's `combined_hash` matches the hash computed from the previous hash and `other_hash`, then checks that the final computed hash equals `self.root_hash()`: [2](#0-1) 

The critical flaw is in `root_hash()`: it returns `self.layers.last().combined_hash` — a value that comes from the proof itself, not from any external trusted source: [3](#0-2) 

This is the direct analog to the external report: just as `PythPriceOracle.updatePrice` decoded token addresses from bytes but never validated them against the expected collateral/credited tokens, `ProofOfInclusion.valid()` validates the internal hash chain but never validates the computed root against an expected committed root. The proof is "valid" as long as it is internally self-consistent, regardless of which tree it actually corresponds to.

The Python binding exposes this method directly to DataLayer consumers: [4](#0-3) 

`MerkleBlob.get_proof_of_inclusion` is also exposed to Python, and the proof it returns is a `Streamable` object that can be re-serialized, transmitted, and deserialized by a peer: [5](#0-4) 

### Impact Explanation

An attacker operating as a DataLayer peer can:

1. Build any Merkle tree of their choosing containing a fabricated key-value pair.
2. Generate a `ProofOfInclusion` for that pair from their own tree.
3. Serialize it via `Streamable` and transmit it to a victim DataLayer node.
4. The victim calls `proof.valid()`, which returns `true` because the hash chain is internally consistent.
5. The victim accepts the proof as evidence that the key-value pair exists in the expected shared DataLayer store — even though it does not.

This allows forging DataLayer inclusion proofs, letting untrusted input prove invalid state. This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type explicitly designed for cross-node transmission. The Python API exposes `valid()` with no root hash parameter, making it the natural and only provided verification method. Any DataLayer node that receives a proof from a peer and calls only `proof.valid()` — without separately asserting `proof.root_hash() == known_committed_root` — is vulnerable. The API design actively invites this mistake by not requiring the expected root as a parameter.

### Recommendation

Modify `valid()` to accept an expected root hash parameter and validate against it:

```rust
pub fn valid(&self, expected_root: &Hash) -> bool {
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
    &existing_hash == expected_root
}
```

This forces every call site to supply the externally-known committed root, eliminating the class of bugs where a caller forgets to separately check `proof.root_hash() == expected_root`.

### Proof of Concept

```rust
use chia_datalayer::{Hash, MerkleBlob, KeyId, ValueId, InsertLocation, ProofOfInclusion, ProofOfInclusionLayer, Side};

// Attacker builds their own tree with a fabricated entry
let mut attacker_blob = MerkleBlob::new(Vec::new()).unwrap();
let fake_key = KeyId(9999);
let fake_value = ValueId(8888);
let fake_hash = [0xAA; 32];
attacker_blob.insert(fake_key, fake_value, &fake_hash, InsertLocation::Auto {}).unwrap();
attacker_blob.calculate_lazy_hashes().unwrap();

// Attacker generates a proof from their own tree
let forged_proof = attacker_blob.get_proof_of_inclusion(fake_key).unwrap();

// Victim calls valid() — returns true, even though this proof
// corresponds to the attacker's tree, not the victim's expected tree
assert!(forged_proof.valid()); // passes — forged proof accepted
// The victim never checked: forged_proof.root_hash() == victim_expected_root
``` [2](#0-1) [3](#0-2)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L242-243)
```text
    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
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
