### Title
`ProofOfInclusion::valid()` Tautological Root Check Allows Forged DataLayer Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary
`ProofOfInclusion::valid()` contains a tautological final check: after verifying internal chain consistency, it compares `existing_hash` against `self.root_hash()`, but `root_hash()` returns `last.combined_hash` — the exact same value `existing_hash` was just set to. The function never verifies the proof against any external trusted root, so any internally self-consistent `ProofOfInclusion` passes validation regardless of what tree it claims to represent.

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
        existing_hash = calculated_hash;   // ← set to last layer's combined_hash
    }
    existing_hash == self.root_hash()      // ← always true: root_hash() returns last.combined_hash
}
``` [1](#0-0) 

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← same value existing_hash was just assigned
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` equals `calculated_hash` from the last iteration, which was already asserted equal to `layer.combined_hash`. `self.root_hash()` returns that same `last.combined_hash`. The final comparison `existing_hash == self.root_hash()` is therefore always `true` when the loop completes without returning `false`. The function only verifies internal self-consistency of the proof chain; it never checks the computed root against any external trusted root value.

The correct pattern for Merkle proof verification requires the verifier to supply a trusted root and check `existing_hash == trusted_root` at the end. This is the standard audited pattern used by every well-known Merkle library. The custom reimplementation here omits that critical step.

`ProofOfInclusion` derives `Streamable` and is exposed to Python via `pyclass` / `pymethods`, meaning it can be deserialized from untrusted bytes and its `valid()` method called directly: [3](#0-2) 

The Python binding exposes no root parameter to `valid()`, so callers have no API-level mechanism to supply a trusted root through this function.

### Impact Explanation

An attacker who can supply a serialized `ProofOfInclusion` to a verifier can forge a proof for any arbitrary `node_hash` by constructing a self-consistent chain of `ProofOfInclusionLayer` values. `valid()` returns `true` for any such proof, regardless of the actual DataLayer tree root. This allows untrusted input to prove invalid state — forged inclusion of keys that do not exist in the committed tree.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and fully exposed to Python. Any code path that receives a `ProofOfInclusion` from an external source (e.g., a DataLayer peer) and calls `proof.valid()` as the sole verification step is vulnerable. The Python test suite calls `proof_of_inclusion.valid()` without any external root check, confirming this is the intended usage pattern. [4](#0-3) 

### Recommendation

Change `valid()` to accept a trusted root parameter and compare the computed root against it:

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
    &existing_hash == trusted_root   // compare against external trusted root
}
```

Update the Python binding to require the trusted root as an argument. All call sites that currently call `proof.valid()` must be updated to supply the known-good tree root obtained from a trusted source (e.g., the on-chain DataLayer store commitment).

### Proof of Concept

```python
from chia_rs import ProofOfInclusion, ProofOfInclusionLayer, bytes32
from chia_sha2 import Sha256

# Forge a proof for an arbitrary node_hash not in any real tree.
fake_node_hash = bytes32(b'\xAB' * 32)
fake_other_hash = bytes32(b'\xCD' * 32)

# Compute what combined_hash must be for internal consistency.
import hashlib
h = hashlib.sha256(b'\x02' + fake_node_hash + fake_other_hash).digest()
fake_combined = bytes32(h)

layer = ProofOfInclusionLayer(
    other_hash_side=...,   # Side.Right
    other_hash=fake_other_hash,
    combined_hash=fake_combined,
)
proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True even though this proof was never generated
# from any real MerkleBlob and corresponds to no committed tree root.
assert proof.valid()   # passes — forged proof accepted
```

The tautological check `existing_hash == self.root_hash()` passes because both sides resolve to `fake_combined`, the attacker-controlled value.

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L86-124)
```rust
    #[test]
    fn test_proof_of_inclusion() {
        let num_repeats = 10;
        let mut seed = 0;

        let mut random = StdRng::seed_from_u64(37);

        let mut merkle_blob = MerkleBlob::new(Vec::new()).unwrap();
        let mut keys_values: HashMap<KeyId, ValueId> = HashMap::new();

        for repeats in 0..num_repeats {
            let num_inserts = 1 + repeats * 100;
            let num_deletes = 1 + repeats * 10;

            let mut kv_ids: Vec<(KeyId, ValueId)> = Vec::new();
            let mut hashes: Vec<Hash> = Vec::new();
            for _ in 0..num_inserts {
                seed += 1;
                let (key, value) = generate_kvid(seed);
                kv_ids.push((key, value));
                hashes.push(generate_hash(seed));
                keys_values.insert(key, value);
            }

            merkle_blob
                .batch_insert(zip(kv_ids, hashes).collect())
                .unwrap();
            merkle_blob.calculate_lazy_hashes().unwrap();

            for kv_id in keys_values.keys().copied() {
                let proof_of_inclusion = match merkle_blob.get_proof_of_inclusion(kv_id) {
                    Ok(proof_of_inclusion) => proof_of_inclusion,
                    Err(error) => {
                        open_dot(merkle_blob.to_dot().unwrap().set_note(&error.to_string()));
                        panic!("here");
                    }
                };
                assert!(proof_of_inclusion.valid());
            }
```
