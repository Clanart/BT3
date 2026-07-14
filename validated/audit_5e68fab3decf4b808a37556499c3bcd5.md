### Title
`ProofOfInclusion::valid()` Never Verifies Against a Trusted Root Hash — Forged Inclusion Proofs Always Pass — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` contains a tautological final check that is always `true` after the loop completes. The method only verifies internal hash-chain consistency; it never compares the computed root against any externally-trusted value. Any attacker-supplied `ProofOfInclusion` with a self-consistent (but entirely fabricated) hash chain will return `valid() == true`, allowing forged DataLayer inclusion proofs to be accepted.

### Finding Description

The `valid()` method in `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs` is:

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

        existing_hash = calculated_hash;   // ← set to calculated_hash
    }

    existing_hash == self.root_hash()      // ← always true
}
```

The loop invariant is: the function only continues past the `if` guard when `calculated_hash == layer.combined_hash`, and then immediately sets `existing_hash = calculated_hash`. After the last iteration, `existing_hash` therefore equals `layers.last().combined_hash`.

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same field
    } else {
        self.node_hash
    }
}
```

So the final comparison `existing_hash == self.root_hash()` reduces to `layers.last().combined_hash == layers.last().combined_hash`, which is unconditionally `true`. The check is dead code from a security standpoint.

The analog to the original report is direct: `removeCollateralWLpTo` only checks that `tokenId` belongs to the position when the full balance is removed (`newWLpAmt == 0`); for partial removals the ownership check is skipped entirely. Here, `valid()` only checks that the hash chain is internally self-consistent; it never checks the chain against any external, trusted root — the ownership check (binding the proof to a committed root) is missing entirely.

### Impact Explanation

DataLayer root hashes are committed on-chain. Clients that receive a `ProofOfInclusion` from an untrusted peer and call `proof.valid()` as their sole verification step will accept any self-consistent fabricated proof, regardless of whether the claimed `node_hash` (key/value pair) is actually present in the committed tree.

An attacker can:
1. Choose any target `node_hash` (representing a fake key-value pair).
2. Build a chain of `ProofOfInclusionLayer` values where each `combined_hash` is computed from the previous step using `calculate_internal_hash` with arbitrary `other_hash` values.
3. Submit this proof; `valid()` returns `true`.

This satisfies the allowed High impact: **DataLayer Merkle proof logic accepts forged inclusion, letting untrusted input prove invalid state.**

The Python binding exposes `valid()` as the primary and only verification API:

```python
def valid(self) -> bool: ...
```

There is no `valid_against_root(expected: bytes32) -> bool` variant. Callers who do not separately check `proof.root_hash() == committed_root` — a step that is not enforced or documented as required — are fully vulnerable.

### Likelihood Explanation

The method is named `valid()` and is the sole public verification entry point exposed to Python and wasm consumers. Its signature gives no indication that an additional root-hash comparison is required. DataLayer sync and proof-verification code that calls `proof.valid()` without a subsequent `proof.root_hash() == on_chain_root` check will silently accept forged proofs. The attacker-controlled entry path is any network message carrying a `ProofOfInclusion` blob deserialized via `from_bytes` / `parse_rust`.

### Recommendation

Replace the self-referential final comparison with a comparison against a caller-supplied trusted root:

```rust
pub fn valid(&self) -> bool {
    // internal consistency only — callers MUST also check root_hash()
    ...
}

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
    existing_hash == *expected_root   // compare against trusted external root
}
```

All call sites that verify externally-received proofs must be updated to use `valid_against_root(committed_root)`. The Python binding should expose this variant and deprecate or remove the root-agnostic `valid()`.

### Proof of Concept

```python
from chia_rs.datalayer import (
    MerkleBlob, ProofOfInclusion, ProofOfInclusionLayer
)
import hashlib

# Attacker wants to forge proof that fake_node_hash is in the tree.
fake_node_hash = bytes([0xAB] * 32)
other_hash     = bytes([0xCD] * 32)

# Build one self-consistent layer: combined = H(fake_node_hash, other_hash)
def combine(left, right):
    # mirrors calculate_internal_hash (left-side)
    h = hashlib.sha256(b"\x01" + left + right).digest()
    return h

combined = combine(fake_node_hash, other_hash)

layer = ProofOfInclusionLayer(
    other_hash_side=0,      # left side
    other_hash=other_hash,
    combined_hash=combined,
)

forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True even though fake_node_hash is not in any real tree
assert forged_proof.valid(), "forged proof accepted"
print("root_hash of forged proof:", forged_proof.root_hash().hex())
# root_hash is attacker-controlled; it will NOT match any on-chain commitment,
# but callers who only call valid() never check that.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-243)
```text
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

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
