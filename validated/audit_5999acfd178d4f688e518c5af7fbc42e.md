### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Committed Root Hash, Enabling Forged Inclusion Proofs - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary
`ProofOfInclusion::valid()` only checks the internal self-consistency of the hash chain within the proof object itself. It never compares the derived root against any externally committed Merkle root. Because `ProofOfInclusion` is `Streamable` and exposed through Python bindings, an attacker can deserialize a crafted proof that passes `valid()` while proving inclusion in an entirely different tree, enabling forged DataLayer inclusion proofs.

### Finding Description

The `valid()` method in `ProofOfInclusion` is the sole verification gate for DataLayer inclusion proofs:

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
``` [1](#0-0) 

The terminal check `existing_hash == self.root_hash()` is **tautological**. `root_hash()` is defined as:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash   // ← derived from the proof itself
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop, `existing_hash` equals the last `calculated_hash`, which was already asserted to equal `layer.combined_hash`. So `existing_hash == self.root_hash()` reduces to `last.combined_hash == last.combined_hash` — always `true`. The function never accepts an external expected root as a parameter and never compares against one.

`ProofOfInclusion` is both `Streamable` (deserializable from raw bytes) and exposed to Python via `PyStreamable` / `from_py_object`:

```rust
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
``` [3](#0-2) 

The Python binding exposes `valid()` directly:

```rust
#[pyo3(name = "valid")]
pub fn py_valid(&self) -> bool {
    self.valid()
}
``` [4](#0-3) 

### Impact Explanation

Any verifier that receives a `ProofOfInclusion` from an untrusted source (network peer, serialized message, Python object) and calls `.valid()` to decide whether a key-value pair is present in a specific DataLayer tree will accept a forged proof. The attacker constructs an internally consistent hash chain for an arbitrary `node_hash` and arbitrary sibling hashes — the chain is self-referential and `valid()` will return `true` regardless of what the actual committed tree root is. This allows an untrusted party to prove inclusion of any key-value pair in any DataLayer tree, corrupting the integrity of DataLayer state proofs.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` and has Python bindings, making it directly reachable from untrusted serialized input. Any DataLayer client or verifier that calls `proof.valid()` after receiving a proof over the network is vulnerable. No privileged access is required — only the ability to send a crafted serialized `ProofOfInclusion` to a verifier.

### Recommendation

`valid()` must accept an expected root hash as a parameter and compare the derived root against it:

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
    &existing_hash == expected_root  // compare against externally committed root
}
```

All call sites — including the Python binding — must be updated to supply the committed tree root. The no-argument `valid()` should be removed or deprecated to prevent misuse.

### Proof of Concept

```python
from chia_rs import ProofOfInclusion, ProofOfInclusionLayer, Hash, Side
import hashlib

# Attacker wants to forge proof that node_hash is in some tree.
# They pick any node_hash and build a self-consistent chain.
node_hash = bytes([0xAA] * 32)
sibling_hash = bytes([0xBB] * 32)

# Compute combined_hash = SHA256(0x00..00 || node_hash || sibling_hash)
# (using calculate_internal_hash logic for Side.Left)
h = hashlib.sha256(b'\x00' * 30 + node_hash + sibling_hash).digest()

layer = ProofOfInclusionLayer(
    other_hash_side=Side.Right,
    other_hash=Hash(sibling_hash),
    combined_hash=Hash(h),
)
forged_proof = ProofOfInclusion(node_hash=Hash(node_hash), layers=[layer])

# valid() returns True even though this root matches no real DataLayer tree
assert forged_proof.valid() == True
# forged_proof.root_hash() == Hash(h), which the attacker chose freely
```

The attacker controls `node_hash`, `sibling_hash`, and therefore `combined_hash` (the claimed root). `valid()` returns `True` for any such construction, with no reference to the actual committed tree root.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L20-29)
```rust
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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L68-71)
```rust
    #[pyo3(name = "valid")]
    pub fn py_valid(&self) -> bool {
        self.valid()
    }
```
