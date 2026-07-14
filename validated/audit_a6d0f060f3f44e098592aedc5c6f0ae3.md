### Title
`ProofOfInclusion::valid()` Does Not Verify Against a Known Root or Claimed Key/Value, Enabling Forged DataLayer Inclusion Proofs - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary
The `ProofOfInclusion::valid()` method is the sole verification API for DataLayer Merkle inclusion proofs. It only checks internal consistency of the proof chain; it never verifies that the proof reaches a specific trusted root, nor that `node_hash` corresponds to the claimed key/value. Critically, the final check `existing_hash == self.root_hash()` is a tautology and provides zero security. An unprivileged attacker can craft a `ProofOfInclusion` from arbitrary bytes (the type is `Streamable`) that passes `valid()` while asserting inclusion of any key/value in any fabricated root.

### Finding Description

**Root cause — tautological final check in `valid()`** [1](#0-0) 

The loop sets `existing_hash = calculated_hash` after verifying `calculated_hash == layer.combined_hash`. After the loop, `existing_hash` therefore equals `self.layers.last().combined_hash`. The `root_hash()` helper returns exactly the same value: [2](#0-1) 

So `existing_hash == self.root_hash()` is always `true` once the loop completes without returning `false`. The check is dead code that provides no security.

**What `valid()` actually checks vs. what it must check**

| Check | Done by `valid()`? |
|---|---|
| Each layer's `combined_hash` is correctly computed from the previous hash and `other_hash` | ✅ |
| `proof.root_hash()` equals a caller-supplied, externally trusted root | ❌ |
| `proof.node_hash` equals the expected leaf hash for the claimed key/value | ❌ |

Because neither of the two security-critical checks is performed, an attacker can construct a `ProofOfInclusion` with:
- An arbitrary `node_hash` (claiming any key/value pair)
- An arbitrary chain of internally consistent layers (producing any desired `root_hash()`)

and `valid()` returns `true`.

**Attacker-controlled entry path**

`ProofOfInclusion` derives `Streamable`: [3](#0-2) 

It is also exposed through Python bindings: [4](#0-3) 

Any DataLayer peer that receives a serialized `ProofOfInclusion` over the network and calls `proof.valid()` — without separately checking `proof.root_hash() == known_root` and `proof.node_hash == expected_leaf_hash(key, value)` — accepts the forged proof unconditionally. The misleading name `valid()` makes this misuse highly likely.

The fuzz target and test suite only exercise proofs generated locally from a `MerkleBlob`, so the forgery path is never exercised: [5](#0-4) [6](#0-5) 

### Impact Explanation
A DataLayer client that relies on `proof.valid()` alone for verification can be tricked into accepting forged proofs. The attacker controls both `node_hash` (the claimed leaf) and the resulting `root_hash()` (the claimed tree root). This enables:
- False data-inclusion claims: asserting that a key maps to a value it does not hold
- Unauthorized state transitions driven by forged DataLayer state
- Cross-node disagreement on DataLayer store contents, corrupting committed state

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

### Likelihood Explanation
The `ProofOfInclusion` struct is a first-class public type with Python bindings, a `Streamable` implementation, and a single verification method named `valid()`. The name strongly implies completeness. No `verify(root, key, value)` helper exists anywhere in the codebase. Any DataLayer integration that receives proofs from external peers and calls `proof.valid()` is vulnerable by default.

### Recommendation
Replace the tautological final check with a comparison against a caller-supplied trusted root, and add a separate check that `node_hash` matches the expected leaf hash. Concretely, change the signature to:

```rust
pub fn verify(&self, expected_root: &Hash, expected_node_hash: &Hash) -> bool {
    if &self.node_hash != expected_node_hash {
        return false;
    }
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
    &existing_hash == expected_root  // compare against externally trusted root
}
```

The existing `valid()` method should either be removed or clearly documented as an internal-consistency-only helper that provides no security guarantee on its own.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side};
use chia_datalayer::merkle::proof_of_inclusion::{ProofOfInclusion, ProofOfInclusionLayer};

fn forged_proof_passes_valid() {
    // Attacker picks any node_hash (claims any key/value)
    let fake_node_hash: Hash = [0x42u8; 32];
    let fake_other_hash: Hash = [0x43u8; 32];

    // Build an internally consistent layer — no knowledge of the real tree needed
    let fake_combined = chia_datalayer::calculate_internal_hash(
        &fake_node_hash,
        Side::Left,
        &fake_other_hash,
    );

    let forged = ProofOfInclusion {
        node_hash: fake_node_hash,   // claims arbitrary key/value
        layers: vec![ProofOfInclusionLayer {
            other_hash_side: Side::Left,
            other_hash: fake_other_hash,
            combined_hash: fake_combined,  // attacker controls root_hash()
        }],
    };

    // valid() returns true — the forged proof passes verification
    assert!(forged.valid());

    // The attacker also controls what root the proof claims to reach
    assert_eq!(forged.root_hash(), fake_combined);
    // fake_combined has nothing to do with any real DataLayer store root
}
```

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L13-18)
```rust
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

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L161-167)
```rust
    #[rstest]
    fn test_proof_of_inclusion_invalid_identified(traversal_blob: MerkleBlob) {
        let mut proof_of_inclusion = traversal_blob.get_proof_of_inclusion(KeyId(307)).unwrap();
        assert!(proof_of_inclusion.valid());
        proof_of_inclusion.layers[1].combined_hash = HASH_ONE;
        assert!(!proof_of_inclusion.valid());
    }
```

**File:** wheel/python/chia_rs/datalayer.pyi (L236-240)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
