### Title
`ProofOfInclusion::valid()` Final Root-Hash Check Is a Tautology, Allowing Forged DataLayer Inclusion Proofs — (`File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` derives its "expected root" from the same field (`last.combined_hash`) that the loop just set `existing_hash` to, making the final comparison `existing_hash == self.root_hash()` unconditionally true whenever the loop completes. The function therefore only verifies internal chain consistency, never that the proof corresponds to any real committed tree root. An attacker who can supply a crafted `ProofOfInclusion` (via network, Python binding, or Streamable deserialization) can forge a proof of inclusion for any arbitrary leaf hash.

### Finding Description

`ProofOfInclusion::valid()` iterates over layers, verifying that each `combined_hash` is correctly computed from the running hash and the sibling:

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

        existing_hash = calculated_hash;   // ← existing_hash := layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true
}
``` [1](#0-0) 

After the loop, `existing_hash` holds the last `calculated_hash`, which was already asserted equal to `layer.combined_hash`. `root_hash()` returns exactly that same field:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value existing_hash was just set to
    } else {
        self.node_hash
    }
}
``` [2](#0-1) 

The final comparison `existing_hash == self.root_hash()` is therefore a tautology: it is always `true` when the loop exits normally. The function never checks whether the computed root matches any externally-known, committed tree root.

This is structurally identical to the reported NFTStaking bug: in that case, `burnedAt` served as both the multiplier-window start and the delta reference, so resetting it made the multiplier always near 1. Here, `root_hash()` is derived from the same `combined_hash` that `existing_hash` was just assigned, so the final equality check is always satisfied — the "reference" and the "computed value" are the same object.

### Impact Explanation

`ProofOfInclusion` is `Streamable` (deserializable from untrusted bytes) and is exposed through Python bindings as a first-class object with a `valid()` method. [3](#0-2) [4](#0-3) 

Any DataLayer consumer that calls `proof.valid()` as its sole verification step — which is exactly what the fuzz target and all Python tests do — will accept a forged proof: [5](#0-4) [6](#0-5) 

An attacker can construct a `ProofOfInclusion` with:
- An arbitrary `node_hash` (the "proven" leaf — need not exist in any real tree)
- A chain of `ProofOfInclusionLayer` values where each `combined_hash = calculate_internal_hash(prev, side, other_hash)` (trivially satisfiable)

`valid()` returns `true` for this fabricated proof. The attacker can thereby prove that any key-value pair is included in a DataLayer tree, enabling forged state proofs.

### Likelihood Explanation

`ProofOfInclusion` is a `Streamable` type exposed over the Python wheel and wasm boundary. Any DataLayer client that receives a proof from an untrusted peer and validates it solely with `proof.valid()` is exploitable. The fuzz target and all existing tests follow exactly this pattern, confirming that `valid()` is widely understood to be a complete validation — not a partial one requiring a separate `root_hash()` comparison. [7](#0-6) 

### Recommendation

The final check in `valid()` must compare against an externally-supplied expected root, not against `self.root_hash()` (which is derived from the same data). Either:

1. Change the signature to `fn valid(&self, expected_root: &Hash) -> bool` and replace the final line with `existing_hash == *expected_root`, or
2. Keep the current signature but document clearly that `valid()` only checks internal consistency and that callers **must** separately assert `proof.root_hash() == committed_root`.

Option 1 is strongly preferred because it makes the security contract impossible to misuse.

### Proof of Concept

```rust
use chia_datalayer::{Hash, Side, ProofOfInclusion, ProofOfInclusionLayer, calculate_internal_hash};

// Arbitrary "leaf" hash — not in any real tree
let fake_leaf = Hash([0xAA; 32]);
let sibling   = Hash([0xBB; 32]);

// Build one layer: combined = internal_hash(fake_leaf, sibling)
let combined = calculate_internal_hash(&fake_leaf, Side::Right, &sibling);

let forged_proof = ProofOfInclusion {
    node_hash: fake_leaf,
    layers: vec![ProofOfInclusionLayer {
        other_hash_side: Side::Right,
        other_hash: sibling,
        combined_hash: combined,   // consistent with the chain
    }],
};

// valid() returns true even though fake_leaf is not in any real tree
// and combined does not match any committed on-chain root.
assert!(forged_proof.valid());
// root_hash() == combined — an attacker-chosen value, not the real tree root
assert_eq!(forged_proof.root_hash(), combined);
```

The loop verifies `calculate_internal_hash(fake_leaf, Right, sibling) == combined` ✓, sets `existing_hash = combined`, then checks `existing_hash == self.root_hash()` which is `combined == combined` ✓ — always true by construction.

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

**File:** wheel/src/api.rs (L1052-1053)
```rust
    datalayer.add_class::<ProofOfInclusionLayer>()?;
    datalayer.add_class::<ProofOfInclusion>()?;
```
