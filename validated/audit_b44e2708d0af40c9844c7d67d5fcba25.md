### Title
Incomplete `HashAlreadyPresent` Guard Allows Leaf-vs-Internal Hash Collision, Corrupting Delta and Hash-Index Logic — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::insert` only checks whether the supplied hash already exists as a **leaf** hash (via `leaf_hash_to_index`). It does not check whether the hash collides with an existing **internal** node's hash. Because `add_internal` never registers internal node hashes in any lookup map, an attacker who supplies a leaf hash equal to an existing internal node's hash bypasses the guard entirely, inserting a structurally valid but semantically corrupt tree state.

---

### Finding Description

**Guard is leaf-only:** [1](#0-0) 

`contains_leaf_hash` delegates to `leaf_hash_to_index.contains_key(hash)`. [2](#0-1) 

**Internal nodes are never registered in any hash map:** [3](#0-2) 

`add_internal` only removes the index from `free_indexes`; it never writes to `leaf_hash_to_index` or any other hash-keyed map. Contrast with `add_leaf`, which writes to both `key_to_index` and `leaf_hash_to_index`: [4](#0-3) 

**Consequence — `get_hashes` silently deduplicates:** [5](#0-4) 

`HashSet::insert` silently drops the duplicate; the returned set has one fewer entry than the actual node count.

**Consequence — `get_hashes_indexes` silently overwrites:** [6](#0-5) 

`HashMap::insert` overwrites the earlier entry; with `leafs_only=false`, one of the two colliding nodes is silently dropped from the index map. This map is consumed by `collect_and_return_from_merkle_blob` to build `node_hash_to_index` for delta computation. [7](#0-6) 

---

### Impact Explanation

The Merkle **root hash** itself is computed bottom-up from child hashes via `calculate_lazy_hashes` and is not read from `get_hashes`, so the root is not directly corrupted. However:

1. **Delta computation is corrupted.** `collect_and_return_from_merkle_blob` builds `node_hash_to_index` by iterating all nodes. A hash collision causes one node's index to be silently overwritten, producing an incorrect delta map. DataLayer sync peers consuming this delta receive a structurally inconsistent view of the tree.

2. **`get_hashes` count invariant breaks.** Any consumer asserting `get_hashes().len() == leaf_count + internal_count` will observe a discrepancy, potentially triggering incorrect state decisions.

3. **`get_hashes_indexes(false)` maps the shared hash to only one node.** Downstream code that uses this map to locate nodes by hash will silently miss one of the two colliding nodes.

---

### Likelihood Explanation

DataLayer trees are public: the full blob is shared among DataLayer peers, so any observer can enumerate all internal node hashes by computing `SHA256(b"\x02" || left_hash || right_hash)`. The `insert` API accepts a caller-supplied `hash: &Hash` with no server-side recomputation from key-value data. A DataLayer store participant (unprivileged relative to consensus) can therefore craft an insert with a known internal node hash and trigger the collision deterministically.

---

### Recommendation

Extend the uniqueness check in `MerkleBlob::insert` to cover all node hashes, not just leaf hashes. One approach: maintain a separate `internal_hash_to_index: HashMap<Hash, TreeIndex>` in `BlockStatusCache`, populated in `add_internal`, and check it alongside `leaf_hash_to_index` before accepting an insert. Alternatively, maintain a single `all_hash_to_index` map covering both node types.

---

### Proof of Concept

```rust
// Build a two-leaf tree so an internal node exists at index 0
let mut blob = MerkleBlob::new(vec![]).unwrap();
blob.insert(KeyId(0), ValueId(0), &hash_a, InsertLocation::AsRoot {}).unwrap();
blob.insert(KeyId(1), ValueId(1), &hash_b, InsertLocation::Leaf {
    index: TreeIndex(0), side: Side::Left,
}).unwrap();

// Read the internal node's hash (root, index 0)
let internal = blob.get_node(TreeIndex(0)).unwrap();
let internal_h = internal.hash(); // = internal_hash(&hash_b, &hash_a)

// Insert a new leaf whose hash equals the internal node's hash
// HashAlreadyPresent check passes because it only checks leaf_hash_to_index
blob.insert(KeyId(2), ValueId(2), &internal_h, InsertLocation::Auto {}).unwrap();

// get_hashes now has a duplicate silently collapsed
let hashes = blob.get_hashes().unwrap();
let node_count = /* leaf_count + internal_count */ 5; // 3 leaves + 2 internals
assert_eq!(hashes.len(), node_count); // FAILS: len == node_count - 1
``` [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** crates/chia-datalayer/src/merkle/blob.rs (L174-176)
```rust
    fn contains_leaf_hash(&self, hash: &Hash) -> bool {
        self.leaf_hash_to_index.contains_key(hash)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L184-193)
```rust
    fn add_internal(&mut self, index: TreeIndex) {
        self.free_indexes.shift_remove(&index);
    }

    fn add_leaf(&mut self, index: TreeIndex, leaf: LeafNode) {
        self.free_indexes.shift_remove(&index);

        self.key_to_index.insert(leaf.key, index);
        self.leaf_hash_to_index.insert(leaf.hash, index);
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L230-293)
```rust
pub fn collect_and_return_from_merkle_blob(
    path: &PathBuf,
    hashes: &HashSet<Hash>,
    known: impl Fn(&Hash) -> bool,
) -> Result<(NodeHashToDeltaReaderNode, NodeHashToIndex), Error> {
    let mut nodes = NodeHashToDeltaReaderNode::new();
    let blob = zstd_decode_path(path)?;
    let mut node_hash_to_index = NodeHashToIndex::new();

    let mut index_to_hash: HashMap<TreeIndex, Hash> = HashMap::new();

    let mut in_subtree: HashSet<Hash> = HashSet::new();
    let mut index_stack: Vec<(TreeIndex, bool)> = Vec::new();
    index_stack.push((TreeIndex(0), false));
    while let Some((index, visited)) = index_stack.pop() {
        let block = format::try_get_block(&blob, index)?;

        let node_hash = block.node.hash();
        index_to_hash.insert(index, node_hash);
        if known(&node_hash) {
            continue;
        }

        match block.node {
            Node::Internal(InternalNode {
                hash, left, right, ..
            }) => {
                if visited {
                    node_hash_to_index.insert(hash, index);
                    if !in_subtree.is_empty() {
                        nodes.insert(
                            hash,
                            deltas::DeltaReaderNode::Internal {
                                left: *index_to_hash.get(&left).unwrap(),
                                right: *index_to_hash.get(&right).unwrap(),
                            },
                        );
                    }

                    in_subtree.remove(&hash);
                } else {
                    if hashes.contains(&hash) {
                        in_subtree.insert(hash);
                    }

                    index_stack.push((index, true));
                    index_stack.push((right, false));
                    index_stack.push((left, false));
                }
            }
            Node::Leaf(LeafNode {
                hash, key, value, ..
            }) => {
                if !in_subtree.is_empty() || hashes.contains(&hash) {
                    nodes.insert(hash, deltas::DeltaReaderNode::Leaf { key, value });
                }

                node_hash_to_index.insert(hash, index);
            }
        }
    }

    Ok((nodes, node_hash_to_index))
}
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L362-374)
```rust
    pub fn insert(
        &mut self,
        key: KeyId,
        value: ValueId,
        hash: &Hash,
        insert_location: InsertLocation,
    ) -> Result<TreeIndex, Error> {
        if self.block_status_cache.contains_key(key) {
            return Err(Error::KeyAlreadyPresent());
        }
        if self.block_status_cache.contains_leaf_hash(hash) {
            return Err(Error::HashAlreadyPresent());
        }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1210-1243)
```rust
    pub fn get_hashes(&self) -> Result<HashSet<Hash>, Error> {
        let mut hashes = HashSet::<Hash>::new();

        if self.blob.is_empty() {
            return Ok(hashes);
        }

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (_, block) = item?;
            hashes.insert(block.node.hash());
        }

        Ok(hashes)
    }

    pub fn get_hashes_indexes(&self, leafs_only: bool) -> Result<HashMap<Hash, TreeIndex>, Error> {
        let mut hash_to_index = HashMap::new();

        if self.blob.is_empty() {
            return Ok(hash_to_index);
        }

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (index, block) = item?;

            if leafs_only && block.metadata.node_type != NodeType::Leaf {
                continue;
            }

            hash_to_index.insert(block.node.hash(), index);
        }

        Ok(hash_to_index)
    }
```
