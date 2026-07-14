### Title
`ProofOfInclusion::valid()` Never Anchors to an External Trusted Root — Forged DataLayer Inclusion Proofs Pass Validation - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only verifies internal hash-chain consistency within the proof itself. Its final check compares `existing_hash` against `self.root_hash()`, which is derived from the proof's own last `combined_hash` field — attacker-controlled data. The function never accepts or checks against an external trusted tree root. Any caller that relies solely on `proof.valid()` to accept DataLayer inclusion can be fooled by a crafted, internally-consistent but fake proof.

### Finding Description

`ProofOfInclusion::valid()` walks the `layers` array, recomputing each `combined_hash` from the bottom up: [1](#0-0) 

After the loop, `existing_hash` holds the last `calculated_hash`, which was already verified to equal `layer.combined_hash` in the same iteration. The final check:

```rust
existing_hash == self.root_hash()
```

is tautological when layers are present, because `root_hash()` returns `self.layers.last().combined_hash`: [2](#0-1) 

`existing_hash` and `self.root_hash()` are the same value — both equal the last `combined_hash` from the proof. The function therefore only verifies that the proof's own hash chain is internally self-consistent. It never compares the computed root against any external, independently-trusted tree root.

The analog to the reported vulnerability is direct:

| Original bug | chia_rs analog |
|---|---|
| `totalStaked` used instead of `totalStaked - totalClaimed` | `self.root_hash()` (from proof) used instead of an external trusted root |
| Limit check uses only one counter, ignoring the other | `valid()` checks against proof-internal data, ignoring the actual committed tree root |

`ProofOfInclusion` is a `Streamable` type exposed through Python bindings: [3](#0-2) 

An attacker can deserialize or construct a `ProofOfInclusion` with arbitrary `node_hash`, `other_hash`, and `combined_hash` values that form a valid internal chain (trivially achievable by computing hashes forward), and `valid()` will return `true` regardless of whether the proof corresponds to the actual DataLayer tree.

### Impact Explanation

**High — DataLayer Merkle proof logic accepts forged inclusion.**

Any Python or Rust caller that uses `proof.valid()` as the sole gate for accepting a DataLayer key-value inclusion claim can be deceived. An attacker supplies a crafted `ProofOfInclusion` for a key that is not in the tree (or is in a different tree with a different root). `valid()` returns `true`. The caller accepts the forged state as proven.

The `root_hash()` method exposed to callers also returns the proof's own claimed root — not an independently verified value — so callers cannot easily distinguish a legitimate proof from a forged one without separately tracking and comparing the actual committed tree root outside the proof object.

### Likelihood Explanation

**Medium.** The `valid()` API name strongly implies complete proof validation. The Python stub documents `valid()` as the validation method with no indication that callers must separately verify `root_hash()` against a trusted external value. Callers who follow the natural API usage pattern are vulnerable. The fuzz target and internal tests only call `valid()` on proofs generated from the same blob, so the tautology is never exercised adversarially. [4](#0-3) 

### Recommendation

`valid()` should accept an external trusted root hash parameter and compare the computed root against it:

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
    &existing_hash == trusted_root  // anchored to external trusted root
}
```

The current `valid()` (internal-consistency-only) should either be removed or clearly documented as insufficient for security-critical inclusion checks. The Python binding should expose the root-anchored variant as the primary validation method.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Forge a proof for an arbitrary node_hash not in any real tree
fake_node_hash = bytes([0xAA] * 32)
fake_other_hash = bytes([0xBB] * 32)

# Compute a combined_hash that is internally consistent
h = hashlib.sha256(b"\x01" + fake_node_hash + fake_other_hash).digest()
fake_combined_hash = bytes(h)

layer = ProofOfInclusionLayer(
    other_hash_side=0,          # left
    other_hash=fake_other_hash,
    combined_hash=fake_combined_hash,
)
proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True — proof was never in any real DataLayer tree
assert proof.valid(), "Forged proof accepted!"
# root_hash() returns the attacker-controlled combined_hash
assert proof.root_hash() == fake_combined_hash
```

The proof passes `valid()` despite corresponding to no real DataLayer tree state. A caller checking only `proof.valid()` would accept this as a legitimate inclusion proof. [1](#0-0) [5](#0-4)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L236-243)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
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
