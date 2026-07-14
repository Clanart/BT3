### Title
`ProofOfInclusion::valid()` Accepts Intermediate (Internal) Node Hashes as Leaf Proofs — (File: `crates/chia-datalayer/src/merkle/proof_of_inclusion.rs`)

### Summary

`ProofOfInclusion::valid()` in the DataLayer Merkle blob library verifies only that the hash chain from `node_hash` up to the root is internally consistent. It never checks that `node_hash` is actually a **leaf** node hash. An attacker who knows the tree structure can construct a `ProofOfInclusion` where `node_hash` is an internal node hash at depth *d*, with a shortened `layers` list covering only the path from depth *d* to the root. `valid()` returns `true` and `root_hash()` returns the correct committed root, so any verifier that relies solely on these two checks accepts the forged proof.

### Finding Description

`ProofOfInclusion::valid()` iterates over `layers`, computing `calculate_internal_hash(existing_hash, side, other_hash)` at each step and comparing it to `layer.combined_hash`. After the loop it checks `existing_hash == self.root_hash()`. [1](#0-0) 

`root_hash()` returns `last.combined_hash` when layers are non-empty: [2](#0-1) 

After the loop, `existing_hash` has already been set to the last `calculated_hash`, which equals `layer.combined_hash` (otherwise the loop would have returned `false`). Therefore the final check `existing_hash == self.root_hash()` is **tautologically true** whenever the loop completes — it adds no additional constraint. The only real check is that each layer's `combined_hash` equals the computed hash. There is no check that `node_hash` is a leaf-type node.

The internal hash function uses a `\x02` prefix: [3](#0-2) 

Leaf hashes in the DataLayer are **user-supplied** (stored verbatim in the leaf block), not domain-separated from internal hashes by the library. This means an internal node hash `H_I = sha256(b"\x02" || left || right)` is a valid 32-byte value that can be placed in `node_hash` without any structural rejection.

`get_proof_of_inclusion` always generates proofs anchored at a leaf: [4](#0-3) 

But `ProofOfInclusion` is a public, directly constructable struct exposed through Python bindings: [5](#0-4) 

It is also `Streamable` (deserializable from raw bytes): [6](#0-5) 

An attacker can therefore craft a `ProofOfInclusion` directly or via `from_bytes` with:
- `node_hash` = hash of any internal node at depth *d*
- `layers` = the *k* layers from depth *d* to the root (a shortened proof, omitting the *d* layers below)

`valid()` returns `true` and `root_hash()` returns the real committed root.

### Impact Explanation

Any DataLayer verifier that calls `proof.valid()` and compares `proof.root_hash()` to a trusted on-chain root — without additionally verifying that `proof.node_hash` corresponds to a known leaf — will accept the forged proof. The attacker can claim that an arbitrary internal-node hash is "included" in the DataLayer store, proving invalid state against a legitimate committed root. This enables forged inclusion proofs: data that is not a leaf of the tree can be presented as proven-included, corrupting DataLayer state verification.

This matches the allowed High impact: **"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."**

### Likelihood Explanation

- The DataLayer tree structure is public (the blob is readable).
- Constructing the attack requires only reading the tree to find any internal node hash and its ancestor path — no cryptographic work.
- `ProofOfInclusion` is constructable directly from Python (`__new__`) and from bytes (`from_bytes`), giving an unprivileged attacker a direct entry path.
- The library provides no documentation warning callers to also validate that `node_hash` is a leaf.

### Recommendation

Add a leaf-type check inside `valid()`. The DataLayer distinguishes leaf nodes from internal nodes via `NodeType` / `NodeMetadata`. One approach:

1. Require callers to pass the expected `node_hash` separately and verify it against a known leaf (e.g., via `get_node_by_hash` which already enforces leaf-only lookup).
2. Or, domain-separate leaf hashes at insertion time (e.g., prefix with `\x01` as the consensus `MerkleSet` does with `TERMINAL`), so an internal node hash structurally cannot equal a valid leaf hash.
3. At minimum, add NatSpec/doc comments on `valid()` explicitly stating it does not verify that `node_hash` is a leaf, and that callers must perform that check independently — mirroring the fix applied to the analogous Scroll `WithdrawTrieVerifier`.

### Proof of Concept

```python
from chia_rs.datalayer import MerkleBlob, ProofOfInclusion, ProofOfInclusionLayer, KeyId, ValueId, Side
import hashlib

# Build a small tree with two leaves
blob = MerkleBlob(blob=bytearray())
h1 = bytes(range(32))
h2 = bytes(reversed(range(32)))
blob.insert(KeyId(1), ValueId(1), h1)
blob.insert(KeyId(2), ValueId(2), h2)
blob.calculate_lazy_hashes()

trusted_root = blob.get_root_hash()

# Get a legitimate proof for key 1 to learn the internal node hash
legit = blob.get_proof_of_inclusion(KeyId(1))
# legit.layers[0].combined_hash is the hash of the internal node
# that is the parent of the two leaves.
internal_node_hash = legit.layers[0].combined_hash  # this IS in the tree, but as an internal node

# If the tree is deeper, take any intermediate combined_hash and
# build a shortened proof from that depth to the root.
# Here the tree has only one internal node, so the shortened proof has 0 layers:
forged = ProofOfInclusion(
    node_hash=internal_node_hash,   # internal node, not a leaf
    layers=[],                       # empty: internal node IS the root
)

assert forged.valid()                          # passes — no leaf check
assert forged.root_hash() == trusted_root      # matches the committed root
print("Forged proof accepted for internal node hash:", internal_node_hash.hex())
``` [1](#0-0) [7](#0-6)

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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L48-55)
```rust
pub fn internal_hash(left_hash: &Hash, right_hash: &Hash) -> Hash {
    let mut hasher = Sha256::new();
    hasher.update(b"\x02");
    hasher.update(left_hash.0);
    hasher.update(right_hash.0);

    Hash(Bytes32::new(hasher.finalize()))
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

**File:** wheel/python/chia_rs/datalayer.pyi (L237-266)
```text
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
