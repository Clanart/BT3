### Title
`Block` Struct Accepts Mismatched `metadata.node_type` and `node` Variant Without Validation — (`File: crates/chia-datalayer/src/merkle/format.rs`)

### Summary

The `Block` struct in the DataLayer Merkle blob implementation stores a `NodeMetadata` (containing a `NodeType` tag: `Internal=0` or `Leaf=1`) and a `Node` enum (`Node::Internal(...)` or `Node::Leaf(...)`) as two independent, unvalidated fields. No check is ever performed to confirm that `metadata.node_type` agrees with the actual `Node` variant held in `node`. When raw bytes are deserialized via `Block::from_bytes()`, the metadata tag alone drives which struct layout is used to parse the data bytes. Crafted blob bytes where the tag disagrees with the data cause the tree to be reconstructed with wrong node classifications, corrupting the Merkle root and enabling forged proofs of inclusion/exclusion.

### Finding Description

`Block` is defined as:

```rust
// TODO: consider forcing ::new() with validity checks
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Block {
    // NOTE: metadata node type and node's type not verified for agreement
    pub metadata: NodeMetadata,
    pub node: Node,
}
``` [1](#0-0) 

The comment explicitly acknowledges the missing invariant. Serialization writes the metadata tag and node data independently:

```rust
pub fn to_bytes(&self) -> Result<BlockBytes, Error> {
    let mut blob: BlockBytes = [0; BLOCK_SIZE];
    blob[METADATA_RANGE].copy_from_slice(&self.metadata.to_bytes()...);
    blob[DATA_RANGE].copy_from_slice(&self.node.to_bytes()?);
    Ok(blob)
}
``` [2](#0-1) 

Deserialization uses only the metadata tag to decide how to parse the data bytes:

```rust
pub fn from_bytes(blob: BlockBytes) -> Result<Self, Error> {
    let metadata = NodeMetadata::from_bytes(&metadata_blob)...;
    let node = Node::from_bytes(&metadata, &data_blob)...;
    Ok(Block { metadata, node })
}
``` [3](#0-2) 

`Node::from_bytes` dispatches entirely on `metadata.node_type`:

```rust
pub fn from_bytes(metadata: &NodeMetadata, blob: &DataBytes) -> Result<Self, ...> {
    Ok(match metadata.node_type {
        NodeType::Internal => Node::Internal(streamable_from_bytes_ignore_extra_bytes(blob)?),
        NodeType::Leaf    => Node::Leaf(streamable_from_bytes_ignore_extra_bytes(blob)?),
    })
}
``` [4](#0-3) 

`InternalNode` is 45 bytes (`hash`=32, `parent`=5, `left`=4, `right`=4) and `LeafNode` is 53 bytes (`hash`=32, `parent`=5, `key`=8, `value`=8), both fitting within `DATA_SIZE = 53`. [5](#0-4) 

If an attacker supplies blob bytes where a block's metadata tag says `NodeType::Internal` but the data bytes encode a `LeafNode`, deserialization will parse the `key` (8 bytes) and `value` (8 bytes) fields as `left` and `right` child `TreeIndex` values. The tree traversal in `BlockStatusCache::new()` will then follow these attacker-controlled child indexes, building a completely wrong cache. [6](#0-5) 

The public entry point is `MerkleBlob::new(blob: Vec<u8>)`, which accepts arbitrary bytes and immediately calls `BlockStatusCache::new(&blob)`: [7](#0-6) 

This is exposed directly to Python callers via the `py-bindings` feature.

### Impact Explanation

An attacker who can supply crafted blob bytes to `MerkleBlob::new()` can:

1. **Corrupt the Merkle root**: `calculate_lazy_hashes()` computes internal node hashes from children. If a node is misclassified (tag says Internal, data is Leaf), the `left`/`right` child indexes are derived from the `key`/`value` bytes, causing `internal_hash()` to be called on wrong child hashes, producing a wrong root. [8](#0-7) 

2. **Forge proofs of inclusion/exclusion**: `get_proof_of_inclusion()` walks the lineage from a leaf to the root using the (now corrupted) cache and node structure. `ProofOfInclusion::valid()` checks that each layer's `combined_hash` matches the computed hash — but if the root hash itself is wrong due to the type mismatch, a crafted proof can pass validation against a corrupted root. [9](#0-8) [10](#0-9) 

3. **Cause the `BlockStatusCache` to misclassify nodes**: A node with tag `NodeType::Leaf` but `Node::Internal` data will be registered in `key_to_index` and `leaf_hash_to_index` with a `key` derived from the internal node's `left`/`right` bytes, poisoning all subsequent key lookups. [11](#0-10) 

This matches the allowed High impact: "DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."

### Likelihood Explanation

The `MerkleBlob::new(blob)` constructor is the primary public API for loading a DataLayer tree from storage or network. Any DataLayer node that loads a blob from an untrusted source (e.g., a peer, a file, a delta sync) is exposed. The `Block` struct and its fields are all `pub`, and the codebase itself acknowledges the missing check with a `// NOTE:` comment and a `// TODO:` on the struct definition. The existing test at line 2575 even constructs a `Block` with mismatched `metadata.node_type` and `node` variant without triggering any validation error (it only fails due to an unrelated out-of-bounds index check). [12](#0-11) 

### Recommendation

1. Add a validation step in `Block::from_bytes()` (or a dedicated `Block::validate()`) that checks `metadata.node_type` agrees with the deserialized `Node` variant, returning an error if they disagree.
2. Enforce the invariant in `Block::to_bytes()` as well, so that a `Block` with a mismatched tag cannot be serialized.
3. Consider making `Block` fields private and providing a constructor that enforces the invariant, as the existing `// TODO: consider forcing ::new() with validity checks` comment suggests. [13](#0-12) 

### Proof of Concept

Craft a blob where block 0 has `metadata = [0x00, 0x00]` (NodeType::Internal, not dirty) but the data bytes encode a `LeafNode` layout (32-byte hash, 5-byte parent, 8-byte key, 8-byte value). When `MerkleBlob::new(crafted_bytes)` is called:

- `Block::from_bytes()` reads `node_type = Internal` and parses the data as `InternalNode`, interpreting the `key` bytes as `left` and `value` bytes as `right` child indexes.
- `BlockStatusCache::new()` follows these attacker-controlled child indexes, reading arbitrary blocks.
- If the crafted blob is self-consistent (all referenced indexes in bounds), the cache is built with wrong node classifications.
- `get_proof_of_inclusion()` generates a proof against a corrupted root.
- `ProofOfInclusion::valid()` returns `true` for a key that was never inserted, or `false` for a key that was.

The `InternalNode` and `LeafNode` structs share the same first 37 bytes (`hash` + `parent`), so the hash field is always read correctly regardless of the tag — only the structural fields (`left`/`right` vs. `key`/`value`) are misinterpreted, making the corruption subtle and hard to detect without explicit tag-vs-variant validation. [14](#0-13)

### Citations

**File:** crates/chia-datalayer/src/merkle/format.rs (L131-136)
```rust
// define the serialized block format
const METADATA_RANGE: Range<usize> = 0..METADATA_SIZE;
pub const METADATA_SIZE: usize = 2;
// TODO: figure out the real max better than trial and error?
pub const DATA_SIZE: usize = 53;
pub const BLOCK_SIZE: usize = METADATA_SIZE + DATA_SIZE;
```

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

**File:** crates/chia-datalayer/src/merkle/format.rs (L216-216)
```rust
// TODO: consider forcing ::new() with validity checks
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

**File:** crates/chia-datalayer/src/merkle/format.rs (L316-322)
```rust
// TODO: consider forcing ::new() with validity checks
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Block {
    // NOTE: metadata node type and node's type not verified for agreement
    pub metadata: NodeMetadata,
    pub node: Node,
}
```

**File:** crates/chia-datalayer/src/merkle/format.rs (L324-331)
```rust
impl Block {
    pub fn to_bytes(&self) -> Result<BlockBytes, Error> {
        let mut blob: BlockBytes = [0; BLOCK_SIZE];
        blob[METADATA_RANGE].copy_from_slice(&self.metadata.to_bytes().map_err(Error::Streaming)?);
        blob[DATA_RANGE].copy_from_slice(&self.node.to_bytes()?);

        Ok(blob)
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L97-130)
```rust
    fn new(blob: &[u8]) -> Result<Self, Error> {
        let index_count = blob.len() / BLOCK_SIZE;

        let mut seen_indexes: BitVec<u64, bitvec::order::Lsb0> = BitVec::repeat(false, index_count);
        let mut key_to_index: HashMap<KeyId, TreeIndex> = HashMap::default();
        let mut leaf_hash_to_index: HashMap<Hash, TreeIndex> = HashMap::default();

        for item in LeftChildFirstIterator::new(blob, None) {
            let (index, block) = item?;
            seen_indexes.set(index.0 as usize, true);

            if let Node::Leaf(leaf) = block.node {
                if key_to_index.insert(leaf.key, index).is_some() {
                    return Err(Error::KeyAlreadyPresent());
                }
                if leaf_hash_to_index.insert(leaf.hash, index).is_some() {
                    return Err(Error::HashAlreadyPresent());
                }
            }
        }

        let mut free_indexes: IndexSet<TreeIndex> = IndexSet::new();
        for (index, seen) in seen_indexes.iter().enumerate() {
            if !seen {
                free_indexes.insert(TreeIndex(index as u32));
            }
        }

        Ok(Self {
            free_indexes,
            key_to_index,
            leaf_hash_to_index,
        })
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

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1024-1027)
```rust
        match block.node {
            Node::Leaf(leaf) => self.block_status_cache.add_leaf(index, leaf),
            Node::Internal(..) => self.block_status_cache.add_internal(index),
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1109-1132)
```rust
    pub fn calculate_lazy_hashes(&mut self) -> Result<(), Error> {
        // OPT: yeah, storing the whole set of blocks via collect is not great
        for item in LeftChildFirstIterator::new_with_block_predicate(
            &self.blob,
            None,
            Some(|block: &Block| block.metadata.dirty),
        )
        .collect::<Vec<_>>()
        {
            let (index, mut block) = item?;
            assert!(block.metadata.dirty);

            let Node::Internal(ref leaf) = block.node else {
                panic!("leaves should not be dirty")
            };
            // OPT: obviously inefficient to re-get/deserialize these blocks inside
            //      an iteration that's already doing that
            let left_hash = self.get_hash(leaf.left)?;
            let right_hash = self.get_hash(leaf.right)?;
            block.update_hash(&left_hash, &right_hash);
            self.insert_entry_to_blob(index, &block)?;
        }

        Ok(())
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1155-1195)
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
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L2575-2602)
```rust
    #[rstest]
    fn test_insert_past_extend_entry_fails(mut small_blob: MerkleBlob) {
        let index = TreeIndex(small_blob.extend_index().0 + 1);
        let block = Block {
            metadata: NodeMetadata {
                node_type: NodeType::Leaf,
                dirty: true,
            },
            node: Node::Internal(InternalNode {
                hash: HASH_ZERO,
                parent: Parent(None),
                left: TreeIndex(0),
                right: TreeIndex(0),
            }),
        };
        let error = small_blob.insert_entry_to_blob(index, &block);

        #[allow(clippy::needless_raw_string_hashes)]
        let expected = expect![[r#"
            Err(
                BlockIndexOutOfBounds(
                    TreeIndex(
                        4,
                    ),
                ),
            )
        "#]];
        expected.assert_debug_eq(&error);
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
