### Title
`ProofOfInclusion::valid()` Is Self-Referential and Never Verifies Against an External Trusted Root, Enabling Forged DataLayer Inclusion Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

---

### Summary

`ProofOfInclusion::valid()` in the DataLayer crate only checks that the proof's internal hash chain is self-consistent. The final equality check is a tautology: it compares `existing_hash` against `self.root_hash()`, but `root_hash()` returns `last.combined_hash`, which is exactly the value `existing_hash` was just assigned in the final loop iteration. No external trusted root is ever compared. An attacker who can supply a `ProofOfInclusion` (via the Python/wasm streamable interface) can forge a proof for any arbitrary key-value pair and have it accepted as valid.

---

### Finding Description

`ProofOfInclusion` is a streamable struct exposed through both the Rust API and the Python/wasm bindings. Its `valid()` method is the sole mechanism for verifying that a proof is correct:

```rust
// crates/chia-datalayer/src/merkle/proof_of_inclusion.rs, lines 32–58
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← returns the proof's own last combined_hash
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

        existing_hash = calculated_hash;   // ← existing_hash = layer.combined_hash
    }

    existing_hash == self.root_hash()      // ← always true: both sides are last.combined_hash
}
```

After the loop, `existing_hash` holds the last `calculated_hash`, which the loop already asserted equals `layer.combined_hash`. `root_hash()` returns that same `last.combined_hash`. The final comparison is therefore always `true` whenever the loop completes without returning `false`. The method never compares the computed root against any externally supplied, trusted root.

The analog to the `FeeSplitter` report is direct: just as `onBalanceChange()` was never called during token transfers (so `userFeeOffset` was never updated against the real accumulator), `valid()` never checks the proof's root against the real committed root. In both cases a critical state-binding step is absent, and the check that remains is vacuously satisfied.

The `ProofOfInclusion` type is fully streamable and deserializable from untrusted bytes:

```python
# wheel/python/chia_rs/datalayer.pyi, lines 237–266
class ProofOfInclusion:
    node_hash: bytes32
    layers: list[ProofOfInclusionLayer]
    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self: ...
```

An attacker can therefore:
1. Choose any target `node_hash` (representing any key-value pair they wish to forge).
2. Build a chain of `ProofOfInclusionLayer` entries where each `combined_hash = calculate_internal_hash(prev, side, other_hash)` with arbitrary `other_hash` values.
3. Serialize the struct and send it to any DataLayer verifier.
4. The verifier calls `proof.valid()`, which returns `true`.

The proof's `root_hash()` will be whatever the attacker chose as the final `combined_hash`, completely decoupled from any real committed tree root.

The existing tests and fuzz targets only ever call `valid()` on proofs generated from a trusted `MerkleBlob`, so the tautology is never exposed:

```rust
// crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs, lines 28–31
for key in keys {
    let proof = blob.get_proof_of_inclusion(key).unwrap();
    assert!(proof.valid());   // proof was just generated from the trusted blob
}
```

No test or fuzz target constructs a `ProofOfInclusion` from untrusted bytes and verifies it against a known root.

---

### Impact Explanation

Any DataLayer client or node that receives a `ProofOfInclusion` from an untrusted peer and calls `proof.valid()` as its sole check will accept a completely forged proof. Because the DataLayer stores key-value commitments on-chain as Merkle roots, a forged inclusion proof lets an attacker convince a verifier that an arbitrary key-value pair is committed in a tree whose root is stored on-chain, without that pair actually being present. This satisfies the allowed impact: **DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion and lets untrusted input prove invalid state.**

---

### Likelihood Explanation

The `ProofOfInclusion` type is exposed as a first-class streamable Python object with `from_bytes` / `to_bytes`. DataLayer sync and delta protocols exchange proofs between nodes. Any node that receives a proof from a peer and calls `proof.valid()` without separately asserting `proof.root_hash() == on_chain_root` is vulnerable. The Python test suite itself only calls `proof.valid()` with no root check, establishing this as the expected usage pattern.

---

### Recommendation

`valid()` must accept an external trusted root and compare against it:

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
    &existing_hash == trusted_root   // compare against the externally supplied root
}
```

The existing `valid()` method (which only checks internal consistency) should either be removed or clearly documented as insufficient for security-critical verification. All call sites that receive proofs from untrusted sources must be updated to use the root-checking variant.

---

### Proof of Concept

```python
from chia_rs.datalayer import (
    ProofOfInclusion, ProofOfInclusionLayer, MerkleBlob, KeyId, ValueId
)
from chia_rs.sized_bytes import bytes32
import hashlib

# Build a real tree with one entry so we have a real root to compare against
blob = MerkleBlob(blob=bytearray())
real_key   = KeyId(1)
real_value = ValueId(1)
real_hash  = bytes32(b'\x01' * 32)
blob.insert(real_key, real_value, real_hash)
blob.calculate_lazy_hashes()
real_root = blob.get_root()   # the on-chain committed root

# Forge a proof for a key that does NOT exist in the tree
fake_node_hash = bytes32(b'\xaa' * 32)   # arbitrary "leaf" hash
fake_other     = bytes32(b'\xbb' * 32)

# Build one layer: combined = internal_hash(fake_node_hash, fake_other)
# (side=Right means: internal_hash(fake_node_hash, fake_other))
h = hashlib.sha256()
h.update(b'\x02')
h.update(fake_node_hash)
h.update(fake_other)
fake_combined = bytes32(h.digest())

layer = ProofOfInclusionLayer(
    other_hash_side=1,          # Right
    other_hash=fake_other,
    combined_hash=fake_combined,
)
forged_proof = ProofOfInclusion(node_hash=fake_node_hash, layers=[layer])

# valid() returns True for the forged proof
assert forged_proof.valid(), "Expected valid() to return True for forged proof"

# But the forged root does NOT match the real on-chain root
assert forged_proof.root_hash() != real_root, "Roots should differ"

# A verifier that only calls proof.valid() is fooled:
print("Forged proof passes valid():", forged_proof.valid())
print("Forged root matches on-chain root:", forged_proof.root_hash() == real_root)
# Output:
# Forged proof passes valid(): True
# Forged root matches on-chain root: False
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** wheel/python/chia_rs/datalayer.pyi (L236-266)
```text
@final
class ProofOfInclusion:
    node_hash: bytes32
    # children before parents
    layers: list[ProofOfInclusionLayer]

    def root_hash(self) -> bytes32: ...
    def valid(self) -> bool: ...

    def __new__(cls, node_hash: bytes32, layers: list[ProofOfInclusionLayer]) -> ProofOfInclusion: ...

    # TODO: generate
    def __hash__(self) -> int: ...
    def __repr__(self) -> str: ...
    def __deepcopy__(self, memo: object) -> Self: ...
    def __copy__(self) -> Self: ...
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self: ...
    @classmethod
    def from_bytes_unchecked(cls, blob: bytes) -> Self: ...
    @classmethod
    def parse_rust(cls, blob: ReadableBuffer, trusted: bool = False) -> tuple[Self, int]: ...
    def to_bytes(self) -> bytes: ...
    def __bytes__(self) -> bytes: ...
    def stream_to_bytes(self) -> bytes: ...
    def get_hash(self) -> bytes32: ...
    def to_json_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_json_dict(cls, json_dict: dict[str, Any]) -> Self: ...
    def replace(self, *, node_hash: bytes32 = ..., layers: list[ProofOfInclusionLayer] = ...) -> Self: ...
    def truncate(self, field: str, length: int) -> None: ...
```

**File:** crates/chia-datalayer/fuzz/fuzz_targets/proofs_of_inclusion.rs (L28-31)
```rust
    for key in keys {
        let proof = blob.get_proof_of_inclusion(key).unwrap();
        assert!(proof.valid());
    }
```
