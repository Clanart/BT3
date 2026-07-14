### Title
DataLayer `ProofOfInclusion::valid()` Does Not Verify Root Against Any Trusted External Value — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle blob implementation only checks that the proof's internal hash chain is self-consistent. It never compares the computed root to any externally-trusted root hash. Because `root_hash()` is derived entirely from the proof's own fields, the final equality check inside `valid()` is a tautology. Any attacker who constructs a self-consistent `ProofOfInclusion` for an arbitrary tree can pass `valid()`, allowing forged inclusion proofs to be accepted by DataLayer clients that rely on this method.

### Finding Description

`ProofOfInclusion::valid()` is implemented as follows:

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
    existing_hash == self.root_hash()   // ← tautology
}
``` [1](#0-0) 

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← taken directly from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop completes without returning `false`, `existing_hash` equals `last.combined_hash` by the loop invariant. `root_hash()` also returns `last.combined_hash`. Therefore `existing_hash == self.root_hash()` is always `true` — the final check is a tautology and adds no security.

The method never accepts an external trusted root hash as a parameter and never compares the computed root to one. Any `ProofOfInclusion` whose internal hash chain is self-consistent will pass `valid()`, regardless of which tree it actually belongs to.

**Contrast with the correct pattern** used in the Python reference implementation for the consensus `MerkleSet`:

```python
def _confirm(root: bytes32, val: bytes, proof: bytes, expected: bool) -> bool:
    p = deserialize_proof(proof)
    if p.get_root() != root:   # ← explicit trusted-root check
        return False
    r, junk = p.is_included_already_hashed(val)
    return r == expected
``` [3](#0-2) 

The DataLayer `ProofOfInclusion` API provides no equivalent mechanism.

**Attacker-controlled entry path**: `ProofOfInclusion` is exposed to Python via `pyclass(get_all, from_py_object)` and derives `Streamable`, meaning it can be deserialized from arbitrary bytes supplied by an untrusted peer. [4](#0-3) 

The Python binding exposes `valid()` directly: [5](#0-4) 

`get_proof_of_inclusion` on `MerkleBlob` is also exposed to Python: [6](#0-5) 

### Impact Explanation

A malicious DataLayer peer can craft a `ProofOfInclusion` that is internally self-consistent (each layer's `combined_hash` equals the hash of its children) but corresponds to a completely different tree than the one the verifying client holds. When the client deserializes this proof and calls `proof.valid()`, the method returns `true`. The client is then deceived into believing a key-value pair is included in a tree when it is not — or that a stale/revoked value is still current. This matches the allowed High impact: **DataLayer Merkle proof logic accepts forged inclusion, letting untrusted input prove invalid state**.

### Likelihood Explanation

DataLayer clients routinely receive `ProofOfInclusion` objects from peers over the network to verify data availability. The `valid()` method's name implies a complete validity check. Callers who do not additionally compare `proof.root_hash()` to a locally-trusted root (a step the API does not prompt for and the fuzz target does not perform) are fully exposed. The fuzz target itself only calls `proof.valid()` without any root comparison: [7](#0-6) 

### Recommendation

1. Add a `valid_for_root(expected_root: &Hash) -> bool` method that replaces the final tautological check with a comparison against the caller-supplied trusted root:

```rust
pub fn valid_for_root(&self, expected_root: &Hash) -> bool {
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
    &existing_hash == expected_root   // compare to externally-trusted root
}
```

2. Deprecate or rename the current `valid()` to `is_internally_consistent()` to make clear it does not verify against any trusted root.

3. Update the Python binding to expose `valid_for_root` and update all callers (fuzz targets, tests, DataLayer client code) to pass the trusted root.

### Proof of Concept

```python
from chia_rs import MerkleBlob, KeyId, ValueId
import hashlib

# Build a legitimate tree with one entry
blob_a = MerkleBlob(blob=bytearray())
blob_a.insert(KeyId(1), ValueId(100), bytes(range(32)))
blob_a.calculate_lazy_hashes()
root_a = blob_a.get_root_hash()

# Build a different tree with a different entry
blob_b = MerkleBlob(blob=bytearray())
blob_b.insert(KeyId(2), ValueId(999), bytes([0xff]*32))
blob_b.calculate_lazy_hashes()

# Attacker obtains a proof from blob_b for key 2
forged_proof = blob_b.get_proof_of_inclusion(KeyId(2))

# The proof is internally self-consistent → valid() returns True
assert forged_proof.valid()  # passes — no root check performed

# But the proof's root does NOT match root_a
assert forged_proof.root_hash() != root_a  # different tree entirely

# A client that only calls valid() is deceived into accepting
# a proof for key 2 / value 999 as belonging to blob_a's tree.
```

The `valid()` call succeeds even though the proof belongs to a completely different tree, demonstrating that forged inclusion proofs pass validation without any external root comparison.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L8-29)
```rust
#[cfg_attr(
    feature = "py-bindings",
    pyclass(get_all, from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
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

**File:** tests/merkle_set.py (L360-368)
```python
def _confirm(root: bytes32, val: bytes, proof: bytes, expected: bool) -> bool:
    try:
        p = deserialize_proof(proof)
        if p.get_root() != root:
            return False
        r, junk = p.is_included_already_hashed(val)
        return r == expected
    except SetError:
        return False
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1542-1548)
```rust
    #[pyo3(name = "get_proof_of_inclusion")]
    pub fn py_get_proof_of_inclusion(
        &self,
        key: KeyId,
    ) -> PyResult<proof_of_inclusion::ProofOfInclusion> {
        Ok(self.get_proof_of_inclusion(key)?)
    }
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
