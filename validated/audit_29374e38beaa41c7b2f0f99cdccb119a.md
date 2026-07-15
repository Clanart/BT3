### Title
`ProofOfInclusion::valid()` Tautological Root-Hash Check Allows Forged DataLayer Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` contains a tautological final comparison that makes it impossible to detect a forged proof: the method only verifies internal chain consistency but never validates the computed root against any external trusted tree root. An attacker who supplies a crafted `ProofOfInclusion` (e.g., via network deserialization through the Python/WASM bindings) can make `valid()` return `true` for any claimed `node_hash`, proving inclusion of arbitrary data in a DataLayer store.

### Finding Description

The `valid()` method in `ProofOfInclusion` is:

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

The `root_hash()` helper is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

**Why the final check is a tautology:**

- The loop invariant enforces `calculated_hash == layer.combined_hash` at every step (returns `false` otherwise).
- After the loop, `existing_hash` holds the last `calculated_hash`, which equals the last `layer.combined_hash`.
- `self.root_hash()` returns `last.combined_hash` when `layers` is non-empty.
- Therefore `existing_hash == self.root_hash()` is **always `true`** when the loop completes.

When `layers` is empty, `existing_hash == self.node_hash` and `root_hash()` also returns `self.node_hash`, so the check is again trivially true.

The method validates only that the proof chain is internally self-consistent. It never compares the computed root against any externally trusted tree root. An attacker can construct a `ProofOfInclusion` with an arbitrary `node_hash` and any set of internally consistent `layers`, and `valid()` will return `true`.

### Impact Explanation

`ProofOfInclusion` is a `Streamable` type exposed via Python bindings as a `pyclass`: [3](#0-2) 

It can be deserialized from attacker-controlled bytes. Any Python or WASM consumer that calls `proof.valid()` without separately asserting `proof.root_hash() == trusted_root` will accept a forged proof. The DataLayer uses these proofs to attest that a key-value pair is committed to an on-chain Merkle root. A forged proof allows an attacker to claim arbitrary data is present in a DataLayer store, letting untrusted input prove invalid state — matching the allowed High impact.

### Likelihood Explanation

The `valid()` method name strongly implies complete proof validation. There is no `valid_for_root(root: Hash)` variant that forces callers to supply the trusted root. The Python binding exposes `valid()` directly: [3](#0-2) 

Any DataLayer client that receives a `ProofOfInclusion` over the network and calls `proof.valid()` as its sole check is vulnerable. The `ProofOfInclusion` struct is `Streamable`, so crafted bytes are the attacker's entry point.

### Recommendation

Replace the tautological final check with a comparison against a caller-supplied trusted root. Change the signature to:

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
    &existing_hash == trusted_root
}
```

Deprecate or remove the no-argument `valid()` method, or redefine it to always return `false` (forcing callers to migrate). Update all call sites — including the Python binding — to pass the trusted root obtained from the on-chain coin or a locally verified `MerkleBlob`.

### Proof of Concept

```python
from chia_rs import ProofOfInclusion, ProofOfInclusionLayer
import hashlib

# Forge a leaf hash for data that is NOT in any real tree
fake_node_hash = bytes([0xAA] * 32)

# Build one internally consistent layer: combined_hash = sha256(0x02 || fake_node_hash || sibling)
sibling = bytes([0xBB] * 32)
combined = hashlib.sha256(b"\x02" + fake_node_hash + sibling).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=1,   # sibling is on the right
    other_hash=sibling,
    combined_hash=combined,
)

proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True even though this key is not in any real DataLayer store
assert proof.valid(), "Forged proof accepted"
print("root claimed by forged proof:", proof.root_hash().hex())
# Caller that only checks proof.valid() is deceived.
```

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L8-18)
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
