### Title
`ProofOfInclusion::valid()` Is a Tautology — Forged DataLayer Inclusion Proofs Always Pass - (File: crates/chia-datalayer/src/merkle/proof_of_inclusion.rs)

### Summary

`ProofOfInclusion::valid()` in the chia-datalayer crate contains a broken invariant: the final equality check `existing_hash == self.root_hash()` is always `true` when the loop completes without returning `false`. The function therefore only verifies internal self-consistency of the proof struct, never that the proof anchors to any actual Merkle tree root. Any caller — including Python/wasm consumers via the exposed binding — that relies on `proof.valid()` to authenticate a received `ProofOfInclusion` is fully bypassable by a crafted proof object.

### Finding Description

`ProofOfInclusion` is a `Streamable` struct (serializable/deserializable across the Python boundary) with two fields:

```
node_hash : Hash          // claimed leaf hash
layers    : Vec<ProofOfInclusionLayer>   // path to root
```

Each `ProofOfInclusionLayer` carries `other_hash`, `other_hash_side`, and `combined_hash`.

The verification function is:

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
```

`root_hash()` is:

```rust
pub fn root_hash(&self) -> Hash {
    if let Some(last) = self.layers.last() {
        last.combined_hash          // ← same value as existing_hash after loop
    } else {
        self.node_hash
    }
}
```

After the loop body executes for the last layer:
- `existing_hash` is set to `calculated_hash`, which was just verified to equal `layer.combined_hash`.
- `self.root_hash()` returns `self.layers.last().combined_hash`, which is that same `layer.combined_hash`.

Therefore `existing_hash == self.root_hash()` is unconditionally `true` whenever the loop finishes without an early `false` return. The final guard is a no-op.

**Consequence:** `valid()` only checks that the `combined_hash` fields in successive layers are mutually consistent with each other. It never compares the computed root against any externally known, trusted tree root. An attacker can freely choose:
- any `node_hash` (claiming any key-value pair is present), and
- any sequence of `layers` whose `combined_hash` fields are internally consistent (trivially constructable by picking arbitrary `other_hash` values and computing `combined_hash = calculate_internal_hash(existing, side, other)`).

The resulting `ProofOfInclusion` will pass `valid()` with `true` regardless of what the actual DataLayer tree root is.

### Impact Explanation

`ProofOfInclusion` is exposed as a `Streamable` Python type via the `py-bindings` feature and the `datalayer.pyi` stub. Any Python DataLayer node that receives a `ProofOfInclusion` over the network and calls `proof.valid()` to decide whether a key-value pair is committed in the tree will accept the forged proof. This lets an untrusted peer prove arbitrary false state — claiming any key maps to any value — without possessing the actual tree or its root. This directly matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion … or lets untrusted input prove invalid state."**

### Likelihood Explanation

The `ProofOfInclusion` struct is `Streamable` (serializable/deserializable), is exposed through Python bindings, and `valid()` is the sole public verification method. Any code path that deserializes a `ProofOfInclusion` from an untrusted source and calls `valid()` — without an additional `proof.root_hash() == known_root` check — is exploitable. Constructing a passing forged proof requires only arithmetic over SHA-256 (no preimage attack needed): the attacker picks arbitrary `other_hash` values and computes the matching `combined_hash` forward from any chosen `node_hash`.

### Recommendation

`valid()` must accept a trusted root hash parameter and compare the computed root against it:

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

    &existing_hash == expected_root   // compare against the caller-supplied root
}
```

All call sites (Rust tests, fuzz target, Python binding) must be updated to supply the actual tree root obtained from a trusted source (e.g., `merkle_blob.get_root_hash()`).

### Proof of Concept

```python
from chia_rs.datalayer import ProofOfInclusion, ProofOfInclusionLayer, Side
import hashlib

# Attacker wants to forge a proof that node_hash X is in the tree.
# Pick any node_hash (the "claimed" leaf).
node_hash = bytes([0xAA] * 32)

# Pick any other_hash for the single layer.
other_hash = bytes([0xBB] * 32)

# Compute combined_hash = internal_hash(other_hash, node_hash)
# (Side.Left means other_hash is on the left)
h = hashlib.sha256()
h.update(b"\x02")          # DataLayer internal node prefix
h.update(other_hash)
h.update(node_hash)
combined_hash = h.digest()

layer = ProofOfInclusionLayer(
    other_hash_side=Side.Left,   # 0
    other_hash=other_hash,
    combined_hash=combined_hash,
)

forged = ProofOfInclusion(node_hash=node_hash, layers=[layer])

# valid() returns True even though this proof was never generated
# from any real MerkleBlob and corresponds to no known tree root.
assert forged.valid() == True   # passes — broken invariant confirmed
```

The forged proof passes `valid()` because `existing_hash` after the loop equals `combined_hash`, and `root_hash()` also returns `combined_hash` — the tautological final check is satisfied by construction. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/chia-datalayer/src/merkle/proof_of_inclusion.rs (L14-29)
```rust
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L57-61)
```rust
pub fn calculate_internal_hash(hash: &Hash, other_hash_side: Side, other_hash: &Hash) -> Hash {
    match other_hash_side {
        Side::Left => internal_hash(other_hash, hash),
        Side::Right => internal_hash(hash, other_hash),
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1196)
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
