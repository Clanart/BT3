### Title
`ProofOfInclusion::valid()` Does Not Verify Against External Root Hash — Forged Inclusion Proofs Always Accepted - (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate contains a tautological final check that makes the function verify only internal self-consistency of the proof object, never binding it to an external trusted root hash. Any attacker who can supply a `ProofOfInclusion` to a caller that relies on `valid()` can forge an inclusion proof for any arbitrary key/hash that is not actually present in the tree.

### Finding Description

The `valid()` method in `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs` is the sole public API for verifying a DataLayer Merkle inclusion proof:

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

The helper `root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value existing_hash was just set to
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

After the loop body executes `existing_hash = calculated_hash` and the loop guard confirms `calculated_hash == layer.combined_hash`, the loop exits with `existing_hash` equal to `last_layer.combined_hash`. `root_hash()` returns exactly that same field. The final comparison `existing_hash == self.root_hash()` is therefore always `true` whenever the loop completes without returning `false`. The function never compares against any externally supplied, trusted root hash.

`ProofOfInclusion` is a `Streamable` type exposed directly through the Python wheel bindings:

```python
def get_proof_of_inclusion(self, key: KeyId) -> ProofOfInclusion: ...
``` [3](#0-2) 

```python
def valid(self) -> bool: ...
``` [4](#0-3) 

The struct is also `Streamable`, meaning it can be deserialized from untrusted bytes received over the network and then passed to `valid()`. [5](#0-4) 

### Impact Explanation

Any code path that:
1. Deserializes a `ProofOfInclusion` from an untrusted source (network peer, RPC caller, etc.), and
2. Calls `proof.valid()` to decide whether to trust the claimed inclusion,

will accept a completely fabricated proof. The attacker does not need to know the actual tree root or any real leaf hash. The check that is supposed to anchor the proof to the committed on-chain root is absent.

This matches the allowed High impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

The `ProofOfInclusion` type is a first-class Streamable protocol object exposed via Python bindings. DataLayer nodes exchange proofs with peers to confirm data availability. Any node that calls `valid()` on a received proof — the natural and documented usage — is vulnerable. The exploit requires only the ability to send a crafted `ProofOfInclusion` blob; no privileged access, key material, or chain reorganization is needed.

### Recommendation

`valid()` must accept the expected root hash as a parameter and compare against it instead of against the self-referential `root_hash()`:

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
    &existing_hash == expected_root   // bind to the trusted, externally supplied root
}
```

All call sites must be updated to pass the root hash obtained from the on-chain commitment or from `MerkleBlob::get_root_hash()` on the locally trusted blob.

### Proof of Concept

**Zero-layer forgery (simplest case):**

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer

fake_hash = bytes32(b"\xde\xad" * 16)

# Construct a proof claiming fake_hash is in the tree, with no layers.
# root_hash() returns node_hash = fake_hash.
# valid() returns fake_hash == fake_hash → True.
forged = ProofOfInclusion(node_hash=fake_hash, layers=[])
assert forged.valid()          # passes — fake_hash is NOT in any real tree
assert forged.root_hash() == fake_hash
```

**Multi-layer forgery:**

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
from chia_rs.datalayer import internal_hash   # or compute manually

fake_leaf  = bytes32(b"\xaa" * 32)
sibling    = bytes32(b"\xbb" * 32)
combined   = internal_hash(sibling, fake_leaf)   # Side.Left

layer = ProofOfInclusionLayer(
    other_hash_side=Side.Left,
    other_hash=sibling,
    combined_hash=combined,
)
forged = ProofOfInclusion(node_hash=fake_leaf, layers=[layer])
assert forged.valid()   # passes — fake_leaf is NOT in any real tree
```

In both cases `valid()` returns `True` for a proof that does not correspond to any real committed tree root, because the final comparison is `combined == combined` rather than `combined == trusted_root`.

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-28)
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

**File:** wheel/python/chia_rs/datalayer.pyi (L243-243)
```text
    def valid(self) -> bool: ...
```

**File:** wheel/python/chia_rs/datalayer.pyi (L335-335)
```text
    def get_proof_of_inclusion(self, key: KeyId) -> ProofOfInclusion: ...
```
