### Title
`ProofOfInclusion::valid()` Does Not Validate Against a Trusted Root — Forged DataLayer Inclusion Proofs Pass Verification - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` only checks the internal hash-chain consistency of the proof. It never accepts or compares against a caller-supplied trusted root. Because `root_hash()` is derived entirely from the proof's own data (`last.combined_hash`), the check is self-referential: any internally-consistent but completely fabricated proof will return `true`. An attacker who can deliver a serialized `ProofOfInclusion` to a DataLayer client can forge proof of inclusion for any key-value pair in any tree.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct exposed to Python via PyO3 bindings. Its `valid()` method walks the layer chain and verifies that each `calculated_hash` equals `layer.combined_hash`, then checks `existing_hash == self.root_hash()`.

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs
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
    existing_hash == self.root_hash()   // root_hash() == last.combined_hash — from the proof itself
}
```

`root_hash()` returns `last.combined_hash` — a field that is part of the proof, not an external trusted value:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // attacker-controlled
    } else {
        self.node_hash
    }
}
```

The final comparison `existing_hash == self.root_hash()` is therefore tautological: it reduces to checking that the last computed hash equals the last stored hash, which is guaranteed to be true for any well-formed (but potentially forged) proof chain. There is no parameter for a trusted root, and no enforcement that the proof's claimed root matches any externally-known committed root.

Contrast this with the correct pattern used in `validate_merkle_proof` for the consensus-layer `MerkleSet`:

```rust
// crates/chia-consensus/src/merkle_tree.rs
pub fn validate_merkle_proof(proof: &[u8], item: &[u8; 32], root: &[u8; 32]) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root {   // ← trusted root comparison
        return Err(SetError);
    }
    Ok(tree.generate_proof(item)?.0)
}
```

The DataLayer `ProofOfInclusion::valid()` is missing this exact check.

The struct is `Streamable` and is directly constructable from untrusted bytes via `from_bytes` / `parse_rust`, and is exported to Python as `chia_rs.datalayer.ProofOfInclusion`. The fuzz harness and all tests call only `proof.valid()` without separately verifying `proof.root_hash()` against a known root:

```rust
// crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs
let proof = blob.get_proof_of_inclusion(key).unwrap();
assert!(proof.valid());   // no root check
```

```python
# tests/test_datalayer.py
proof_of_inclusion = merkle_blob.get_proof_of_inclusion(kv_id)
assert proof_of_inclusion.valid()   # no root check
```

### Impact Explanation

A DataLayer client that receives a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` will accept any internally-consistent fabricated proof, regardless of which tree it actually proves inclusion in. An attacker can:

1. Construct a fake `ProofOfInclusion` with arbitrary `node_hash`, `other_hash`, and `combined_hash` values that form a self-consistent chain.
2. Serialize it via `Streamable` and deliver it to a verifying client.
3. The client calls `proof.valid()` → `true`, and accepts the forged proof as proving that a key-value pair exists in the DataLayer tree.

This lets untrusted input prove invalid state — matching the "High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state" impact.

### Likelihood Explanation

The `valid()` method is the sole verification API on `ProofOfInclusion`. Its name strongly implies complete validation. The Python type stub declares only `valid()` and `root_hash()` with no documentation that callers must separately compare `root_hash()` against a trusted value. Any DataLayer client that follows the natural API usage pattern — deserialize proof, call `valid()` — is vulnerable. The struct is fully serializable and network-deliverable.

### Recommendation

Add a `trusted_root` parameter to `valid()` (or add a separate `valid_for_root(root: &Hash) -> bool` method) that compares `self.root_hash()` against the caller-supplied trusted root as the final step:

```rust
pub fn valid_for_root(&self, trusted_root: &Hash) -> bool {
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
    existing_hash == *trusted_root   // compare against externally-known root
}
```

Expose this as the primary Python API and deprecate the root-less `valid()`. Update all call sites (fuzz targets, tests, Python bindings) to supply the trusted root.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
import os

# Build a real tree with one entry
blob = MerkleBlob(bytearray())
real_key = KeyId(1)
blob.insert(real_key, ValueId(100), os.urandom(32))
blob.calculate_lazy_hashes()
trusted_root = blob.get_root_hash()

# Attacker forges a proof for a key that does NOT exist in the tree
# by constructing a self-consistent chain with an arbitrary root
fake_leaf_hash = bytes([0xAA] * 32)
fake_sibling   = bytes([0xBB] * 32)
# compute combined = calculate_internal_hash(fake_leaf_hash, side=0, fake_sibling)
# (attacker computes this offline)
import hashlib
combined = hashlib.sha256(b"\x01" + fake_leaf_hash + fake_sibling).digest()  # simplified

fake_layer = ProofOfInclusionLayer(
    other_hash_side=0,
    other_hash=fake_sibling,
    combined_hash=combined,   # attacker sets combined_hash to match their computed value
)
forged_proof = ProofOfInclusion(node_hash=fake_leaf_hash, layers=[fake_layer])

# valid() returns True even though this proof has nothing to do with trusted_root
assert forged_proof.valid()                          # True — BUG
assert forged_proof.root_hash() != trusted_root      # True — different tree entirely
# A correct API would require: forged_proof.valid_for_root(trusted_root) → False
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

**File:** crates/chia-consensus/src/merkle_tree.rs (L334-344)
```rust
pub fn validate_merkle_proof(
    proof: &[u8],
    item: &[u8; 32],
    root: &[u8; 32],
) -> Result<bool, SetError> {
    let tree = MerkleSet::from_proof(proof)?;
    if tree.get_root() != *root {
        return Err(SetError);
    }
    Ok(tree.generate_proof(item)?.0)
}
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
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
