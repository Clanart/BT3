### Title
`ProofOfInclusion::valid()` Validates Only Internal Consistency, Not Against a Committed Root — Forged Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in `chia-datalayer` derives the expected root hash from the proof's own last `combined_hash` field rather than from any externally committed root. The final equality check is therefore a tautology: after the loop succeeds, `existing_hash` is always equal to `self.root_hash()` by construction. An attacker who supplies a `ProofOfInclusion` with an arbitrary `node_hash` and a self-consistent chain of `other_hash`/`combined_hash` values will always receive `true` from `valid()`, regardless of what the actual committed DataLayer tree root is.

### Finding Description

`ProofOfInclusion::valid()` performs two steps:

1. It walks `self.layers`, computing `calculated_hash = internal_hash(existing_hash, layer.other_hash_side, layer.other_hash)` and asserting `calculated_hash == layer.combined_hash`. If any layer fails, it returns `false`.
2. After the loop, it checks `existing_hash == self.root_hash()`.

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken directly from the proof itself
    } else {
        self.node_hash
    }
}
```

After the loop completes without returning `false`, `existing_hash` holds the last `calculated_hash`, which was already verified to equal `last_layer.combined_hash`. Therefore `existing_hash == self.root_hash()` reduces to `last_layer.combined_hash == last_layer.combined_hash` — always `true`. The function never compares the computed root against any externally committed tree root.

An attacker can construct a `ProofOfInclusion` for any arbitrary `node_hash` (e.g., the hash of a fake key-value pair) by:
1. Choosing any `node_hash`.
2. Choosing any `other_hash` values and `other_hash_side` values.
3. Computing each `combined_hash` correctly from the previous level.
4. Submitting this proof — `valid()` returns `true`.

This is the direct analog of the external report's pattern: the TWAP check (internal chain consistency) passes, but the critical value used for the final decision (the root) is read from the manipulable source (the proof itself) rather than from a committed external reference.

### Impact Explanation

Any DataLayer client or protocol component that calls `proof.valid()` as the sole check before accepting a proof of inclusion will accept forged proofs for arbitrary key-value pairs. This allows an untrusted peer to prove that any key-value pair is included in a DataLayer tree, regardless of the actual committed root. This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, letting untrusted input prove invalid state.**

The Python binding exposes `valid()` directly as the primary validation interface, making it the natural single call a consumer would make.

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type exposed over the Python/wasm boundary. DataLayer nodes exchange proofs over the network. Any receiver that calls `proof.valid()` without separately asserting `proof.root_hash() == known_committed_root` is exploitable by any peer. The API name `valid()` strongly implies completeness, making accidental misuse highly likely.

### Recommendation

`valid()` must accept an external committed root as a parameter and compare against it, rather than deriving the root from the proof itself:

```rust
pub fn valid_against_root(&self, committed_root: &Hash) -> bool {
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
    &existing_hash == committed_root  // compare against external root
}
```

The existing `valid()` method (and its Python binding) should either be removed or deprecated with a clear warning that it does not verify against any committed root.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};
use chia_datalayer::blob::calculate_internal_hash;

// Attacker wants to forge a proof that fake_node_hash is in the tree.
let fake_node_hash = Hash(/* arbitrary bytes */);
let fake_other_hash = Hash(/* arbitrary bytes */);

// Compute combined_hash honestly so the chain is internally consistent.
let combined = calculate_internal_hash(&fake_node_hash, Side::Right, &fake_other_hash);

let forged_proof = ProofOfInclusion {
    node_hash: fake_node_hash,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Right,
        other_hash: fake_other_hash,
        combined_hash: combined,  // correctly computed
    }],
};

// valid() returns true even though fake_node_hash is not in any real tree.
assert!(forged_proof.valid());
// root_hash() returns `combined`, not the real committed root.
assert_eq!(forged_proof.root_hash(), combined);
```

The tautological final check in `valid()` is confirmed at: [1](#0-0) 

`root_hash()` reads from the proof's own data, not from any external commitment: [2](#0-1) 

The Python binding exposes `valid()` as the primary interface: [3](#0-2) 

`get_proof_of_inclusion` in `MerkleBlob` generates proofs that are consumed by callers who are expected to call `valid()`: [4](#0-3)

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
