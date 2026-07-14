### Title
DataLayer `MerkleBlob` Node Type Confusion via Unvalidated `metadata.node_type` Discriminator — (File: `crates/chia-datalayer/src/merkle/format.rs`)

---

### Summary
The `Block` struct in the DataLayer Merkle blob implementation explicitly acknowledges that `metadata.node_type` and the actual `node` variant are never verified for agreement. Because `MerkleBlob::new()` accepts arbitrary bytes and only validates blob length, an attacker who can supply a crafted blob can produce type-confused blocks where the metadata discriminator says `NodeType::Leaf` but the data bytes encode an `InternalNode` layout (or vice versa). This causes the `BlockStatusCache` to register a phantom leaf with an attacker-controlled key, enabling forged DataLayer proofs of inclusion.

---

### Finding Description

**Root cause — `Block` struct, `format.rs`:**

The `Block` struct carries both a `NodeMetadata` (containing the `node_type` discriminator) and a `Node` enum variant, but the code explicitly notes they are never cross-validated:

```rust
// NOTE: metadata node type and node's type not verified for agreement
pub struct Block {
    pub metadata: NodeMetadata,
    pub node: Node,
}
``` [1](#0-0) 

`Block::from_bytes` uses `metadata.node_type` as the sole discriminator to choose which Streamable type to deserialize the data bytes into:

```rust
pub fn from_bytes(blob: BlockBytes) -> Result<Self, Error> {
    let metadata = NodeMetadata::from_bytes(&metadata_blob)...;
    let node = Node::from_bytes(&metadata, &data_blob)...;
    Ok(Block { metadata, node })
}
``` [2](#0-1) 

`Node::from_bytes` dispatches purely on `metadata.node_type`:

```rust
pub fn from_bytes(metadata: &NodeMetadata, blob: &DataBytes) -> Result<Self, ...> {
    Ok(match metadata.node_type {
        NodeType::Internal => Node::Internal(streamable_from_bytes_ignore_extra_bytes(blob)?),
        NodeType::Leaf    => Node::Leaf(streamable_from_bytes_ignore_extra_bytes(blob)?),
    })
}
``` [3](#0-2) 

**Structural layout mismatch:**

`InternalNode` and `LeafNode` share the same prefix (`hash: Hash` = 32 bytes, `parent: Parent` = 5 bytes) but differ in their trailing fields:

| Type | Trailing bytes |
|---|---|
| `InternalNode` | `left: TreeIndex` (4 B) + `right: TreeIndex` (4 B) = 8 B |
| `LeafNode` | `key: KeyId` (8 B) + `value: ValueId` (8 B) = 16 B | [4](#0-3) 

If an attacker provides a blob block where `metadata.node_type = NodeType::Leaf` (byte value `1`) but the data bytes encode an `InternalNode`, `streamable_from_bytes_ignore_extra_bytes::<LeafNode>` will read:
- `key` = the 8 bytes that are actually `left_index (u32) || right_index (u32)`, interpreted as a big-endian `i64`
- `value` = the next 8 bytes (zero padding from `Node::to_bytes`'s `resize`)

The attacker fully controls `key` by choosing the `left`/`right` child index values in the crafted blob.

**No structural validation in `MerkleBlob::new()`:**

`MerkleBlob::new()` only checks that the blob length is a multiple of `BLOCK_SIZE`. It does not validate node type consistency, hash chain correctness, or tree structure:

```rust
pub fn new(blob: Vec<u8>) -> Result<Self, Error> {
    let remainder = length % BLOCK_SIZE;
    if remainder != 0 { return Err(Error::InvalidBlobLength(remainder)); }
    let block_status_cache = BlockStatusCache::new(&blob)?;
    ...
}
``` [5](#0-4) 

The `check_integrity` function exists but is only enabled in test builds (`check_integrity_on_drop: cfg!(test)`), so it is never called in production: [6](#0-5) 

**`BlockStatusCache` registers the phantom leaf:**

`BlockStatusCache::new()` traverses the tree and, upon encountering a block with `metadata.node_type = NodeType::Leaf`, inserts the deserialized `leaf.key` and `leaf.hash` into its lookup maps without any further validation:

```rust
if let Node::Leaf(leaf) = block.node {
    if key_to_index.insert(leaf.key, index).is_some() {
        return Err(Error::KeyAlreadyPresent());
    }
    if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
        return Err(Error::HashAlreadyPresent());
    }
}
``` [7](#0-6) 

The phantom leaf (whose `key` is derived from the `left`/`right` child index bytes of the underlying `InternalNode`) is now registered as a real leaf. Subsequent calls to `get_proof_of_inclusion` for that key will succeed and produce a structurally valid `ProofOfInclusion`, since `ProofOfInclusion::valid()` only checks the hash chain:

```rust
pub fn valid(&self) -> bool {
    let mut existing_hash = self.node_hash;
    for layer in &self.layers {
        let calculated_hash = calculate_internal_hash(...);
        if calculated_hash != layer.combined_hash { return false; }
        existing_hash = calculated_hash;
    }
    existing_hash == self.root_hash()
}
``` [8](#0-7) 

A crafted blob where all internal node hashes are correctly computed from their children will produce a proof that passes `valid()` for a key that was never actually inserted.

---

### Impact Explanation
An attacker who can supply a crafted blob to `MerkleBlob::new()` (exposed via the Python binding `py_init`) can forge a `ProofOfInclusion` for an arbitrary `KeyId` that was never inserted into the DataLayer tree. The forged proof passes the `ProofOfInclusion::valid()` check, allowing untrusted input to prove invalid state — a DataLayer Merkle proof forgery. [9](#0-8) 

---

### Likelihood Explanation
The `MerkleBlob` Python binding accepts arbitrary bytes. Any DataLayer code path that constructs a `MerkleBlob` from peer-supplied or user-supplied bytes (e.g., during DataLayer sync) is a reachable entry point. The crafted blob requires only that the length is a multiple of `BLOCK_SIZE` and that the hash chain is self-consistent — both are straightforward to satisfy. No privileged access is required.

---

### Recommendation
1. **Add discriminator consistency check in `Block::from_bytes`**: After deserializing, verify that `metadata.node_type` matches the actual `Node` variant (i.e., `NodeType::Leaf` ↔ `Node::Leaf`, `NodeType::Internal` ↔ `Node::Internal`). Return an error on mismatch.
2. **Enable `check_integrity` in production**: Remove the `cfg!(test)` guard on `check_integrity_on_drop`, or call `check_integrity()` inside `MerkleBlob::new()` when accepting externally-supplied blobs.
3. **Remove the `TODO` comment** at `Block` definition and enforce validity through a constructor (`Block::new`) that rejects mismatched metadata/node pairs.

---

### Proof of Concept

Craft a blob with one internal node (index 0, root) and one type-confused block (index 1):

```
Block 0 (Internal, root):
  metadata: [0x00, 0x00]  (NodeType::Internal, dirty=false)
  data:     hash=H_root, parent=None, left=1, right=<unused>

Block 1 (type-confused):
  metadata: [0x01, 0x00]  (NodeType::Leaf, dirty=false)
  data:     hash=H_leaf, parent=Some(0),
            [bytes 37-40] = 0x00000002  <- left child index (read as first 4 bytes of key)
            [bytes 41-44] = 0x00000003  <- right child index (read as last 4 bytes of key)
            [bytes 45-52] = 0x00...     <- value = 0
```

`MerkleBlob::new(crafted_blob)` succeeds (length is a multiple of `BLOCK_SIZE`).  
`BlockStatusCache` registers block 1 as a leaf with `key = 0x0000000200000003` (= `8589934595i64`).  
`get_proof_of_inclusion(KeyId(8589934595))` returns a `ProofOfInclusion` that passes `valid()`, proving inclusion of a key that was never inserted.

### Citations

**File:** crates/chia-datalayer/src/merkle/format.rs (L173-214)
```rust
#[derive(Copy, Clone, Debug, Hash, PartialEq, Eq, Streamable)]
pub struct InternalNode {
    pub hash: Hash,
    pub parent: Parent,
    pub left: TreeIndex,
    pub right: TreeIndex,
}

impl InternalNode {
    pub fn sibling_index(&self, index: TreeIndex) -> Result<TreeIndex, Error> {
        if index == self.right {
            Ok(self.left)
        } else if index == self.left {
            Ok(self.right)
        } else {
            Err(Error::IndexIsNotAChild(index))
        }
    }

    pub fn get_sibling_side(&self, index: TreeIndex) -> Result<Side, Error> {
        if self.left == index {
            Ok(Side::Right)
        } else if self.right == index {
            Ok(Side::Left)
        } else {
            Err(Error::IndexIsNotAChild(index))
        }
    }
}

#[cfg_attr(
    feature = "py-bindings",
    pyclass(get_all, from_py_object),
    derive(PyJsonDict, PyStreamable)
)]
#[derive(Copy, Clone, Debug, Hash, PartialEq, Eq, Streamable)]
pub struct LeafNode {
    pub hash: Hash,
    pub parent: Parent,
    pub key: KeyId,
    pub value: ValueId,
}
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L252-260)
```rust
    pub fn from_bytes(
        metadata: &NodeMetadata,
        blob: &DataBytes,
    ) -> Result<Self, chia_traits::chia_error::Error> {
        Ok(match metadata.node_type {
            NodeType::Internal => Node::Internal(streamable_from_bytes_ignore_extra_bytes(blob)?),
            NodeType::Leaf => Node::Leaf(streamable_from_bytes_ignore_extra_bytes(blob)?),
        })
    }
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L317-322)
```rust
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Block {
    // NOTE: metadata node type and node's type not verified for agreement
    pub metadata: NodeMetadata,
    pub node: Node,
}
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L333-341)
```rust
    pub fn from_bytes(blob: BlockBytes) -> Result<Self, Error> {
        let metadata_blob: MetadataBytes = blob[METADATA_RANGE].try_into().unwrap();
        let data_blob: DataBytes = blob[DATA_RANGE].try_into().unwrap();
        let metadata =
            NodeMetadata::from_bytes(&metadata_blob).map_err(Error::FailedLoadingMetadata)?;
        let node = Node::from_bytes(&metadata, &data_blob).map_err(Error::FailedLoadingNode)?;

        Ok(Block { metadata, node })
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L108-115)
```rust
            if let Node::Leaf(leaf) = block.node {
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
                if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
                    return Err(Error::HashAlreadyPresent());
                }
            }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L316-332)
```rust
    pub fn new(blob: Vec<u8>) -> Result<Self, Error> {
        let length = blob.len();
        let remainder = length % BLOCK_SIZE;
        if remainder != 0 {
            return Err(Error::InvalidBlobLength(remainder));
        }

        let block_status_cache = BlockStatusCache::new(&blob)?;

        let self_ = Self {
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1371-1386)
```rust
#[cfg(feature = "py-bindings")]
#[pymethods]
impl MerkleBlob {
    #[allow(clippy::needless_pass_by_value)]
    #[new]
    pub fn py_init(blob: PyBuffer<u8>) -> PyResult<Self> {
        assert!(
            blob.is_c_contiguous(),
            "from_bytes() must be called with a contiguous buffer"
        );
        #[allow(unsafe_code)]
        let slice =
            unsafe { std::slice::from_raw_parts(blob.buf_ptr() as *const u8, blob.len_bytes()) };

        Ok(Self::new(Vec::from(slice))?)
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
