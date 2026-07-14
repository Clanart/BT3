Looking at the code carefully to trace the exact path.

### Title
Unvalidated Internal Node Hash in `inner_build_blob_from_node_list` Allows Forged DataLayer Tree Root — (`crates/chia-datalayer/src/merkle/blob.rs`)

---

### Summary

`MerkleBlob::inner_build_blob_from_node_list` stores the caller-supplied map key directly as `InternalNode.hash` without recomputing it from children via `internal_hash(left, right)`. Because `DeltaReader::new` also performs no hash-correctness validation, an attacker who controls the `internal_nodes` input can produce a `MerkleBlob` whose root (and any internal node) carries an arbitrary forged hash, violating the invariant `hash == sha256(0x02 || left_hash || right_hash)`.

---

### Finding Description

**Step 1 — No validation in `DeltaReader::new`**

`DeltaReader::new` inserts every `(hash, (left, right))` pair from `internal_nodes` verbatim into the node map. There is no check that `hash == internal_hash(left, right)`. [1](#0-0) 

**Step 2 — `inner_build_blob_from_node_list` uses the map key as the stored hash**

For an `Internal` variant, the function uses `node_hash` (the map key, i.e. the attacker-supplied value) directly as `InternalNode.hash`. It never calls `internal_hash` on the resolved child hashes. [2](#0-1) 

The node is written with `dirty: false`, so `calculate_lazy_hashes` will never revisit and recompute it. [3](#0-2) 

**Step 3 — `check_integrity` / `check_just_integrity` do not detect the inconsistency**

`check_just_integrity` only verifies structural properties (parent-child index relationships, node counts, cache consistency). It never asserts that `internal_node.hash == internal_hash(left_child.hash, right_child.hash)`. [4](#0-3) 

`check_integrity` calls `calculate_lazy_hashes` on a clone, but because the forged nodes are `dirty: false`, no hash is recomputed there either. [5](#0-4) 

**Step 4 — Public Python binding exposes the full path**

`DeltaReader` is exposed to Python via `py_init`, making the attack reachable from any Python-level DataLayer sync code that passes peer-supplied delta data. [6](#0-5) 

---

### Impact Explanation

A malicious DataLayer peer (or any caller who controls `internal_nodes`) can produce a `MerkleBlob` whose root hash is an arbitrary value unrelated to the actual leaf content. Downstream consumers of `get_hash_at_index(TreeIndex(0))` or `get_proof_of_inclusion` will operate against this forged root, allowing:

- Acceptance of a forged tree root as the canonical DataLayer state.
- Generation of proofs of inclusion that verify against a fake root, enabling invalid state to appear valid.
- Corruption of any DataLayer store that persists the resulting blob.

This matches the allowed High impact: *"DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state."*

---

### Likelihood Explanation

The `DeltaReader` is the standard mechanism for DataLayer delta sync. Any peer participating in DataLayer sync can supply crafted `internal_nodes`. No privilege, key material, or chain reorg is required. The Python binding makes this trivially reachable from application code.

---

### Recommendation

In `inner_build_blob_from_node_list`, after resolving `left_index` and `right_index`, recompute the internal hash from the actual child hashes and either:

1. **Assert/reject** if `node_hash != internal_hash(left_child_hash, right_child_hash)`, returning an error; or
2. **Always recompute** the hash from children and ignore the supplied key hash for the stored `InternalNode.hash`.

Additionally, add a hash-correctness check to `check_just_integrity` that verifies every internal node's stored hash equals `internal_hash` of its children's stored hashes.

---

### Proof of Concept

```python
from chia_rs import MerkleBlob
from chia_datalayer import DeltaReader

leaf1_hash = bytes([0x01] * 32)
leaf2_hash = bytes([0x02] * 32)
fake_root  = bytes([0xFF] * 32)  # NOT internal_hash(leaf1, leaf2)

dr = DeltaReader(
    internal_nodes={fake_root: (leaf1_hash, leaf2_hash)},
    leaf_nodes={
        leaf1_hash: (1, 10),
        leaf2_hash: (2, 20),
    }
)

blob = dr.create_merkle_blob_and_filter_unused_nodes(
    fake_root, {fake_root, leaf1_hash, leaf2_hash}
)

root = blob.get_root_hash()
assert root == fake_root  # passes — forged hash accepted as root
# internal_hash(leaf1_hash, leaf2_hash) != fake_root, invariant broken
``` [7](#0-6)

### Citations

**File:** crates/chia-datalayer/src/merkle/deltas.rs (L74-85)
```rust
    pub fn new(internal_nodes: InternalNodesMap, leaf_nodes: LeafNodesMap) -> Result<Self, Error> {
        let mut nodes = NodeHashToDeltaReaderNode::new();

        for (hash, (left, right)) in internal_nodes {
            nodes.insert(hash, DeltaReaderNode::Internal { left, right });
        }
        for (hash, (key, value)) in leaf_nodes {
            nodes.insert(hash, DeltaReaderNode::Leaf { key, value });
        }

        Ok(Self { nodes })
    }
```

**File:** crates/chia-datalayer/src/merkle/deltas.rs (L199-205)
```rust
#[cfg(feature = "py-bindings")]
#[pymethods]
impl DeltaReader {
    #[new]
    pub fn py_init(internal_nodes: InternalNodesMap, leaf_nodes: LeafNodesMap) -> PyResult<Self> {
        Ok(Self::new(internal_nodes, leaf_nodes)?)
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L812-819)
```rust
    pub fn check_integrity(&self) -> Result<(), Error> {
        self.check_just_integrity()?;

        let mut clone = self.clone();
        clone.check_integrity_on_drop = false;
        clone.calculate_lazy_hashes()?;
        clone.check_just_integrity()
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L821-887)
```rust
    fn check_just_integrity(&self) -> Result<(), Error> {
        let mut leaf_count: usize = 0;
        let mut internal_count: usize = 0;
        let mut child_to_parent: HashMap<TreeIndex, TreeIndex> = HashMap::new();

        for item in ParentFirstIterator::new(&self.blob, None) {
            let (index, block) = item?;
            if let Some(parent) = block.node.parent().0 {
                if child_to_parent.remove(&index) != Some(parent) {
                    return Err(Error::IntegrityParentChildMismatch(index));
                }
            }
            match block.node {
                Node::Internal(node) => {
                    internal_count += 1;
                    child_to_parent.insert(node.left, index);
                    child_to_parent.insert(node.right, index);
                }
                Node::Leaf(node) => {
                    leaf_count += 1;
                    let cached_index = self
                        .block_status_cache
                        .get_index_by_key(node.key)
                        .ok_or(Error::IntegrityKeyNotInCache(node.key))?;
                    if *cached_index != index {
                        return Err(Error::IntegrityKeyToIndexCacheIndex(
                            node.key,
                            index,
                            *cached_index,
                        ));
                    }
                    assert!(
                        !self.block_status_cache.is_index_free(index),
                        "{}",
                        format!("active index found in free index list: {index:?}")
                    );
                }
            }
        }

        let key_to_index_cache_length = self.block_status_cache.key_to_index.len();
        if leaf_count != key_to_index_cache_length {
            return Err(Error::IntegrityKeyToIndexCacheLength(
                leaf_count,
                key_to_index_cache_length,
            ));
        }
        let leaf_hash_to_index_cache_length = self.block_status_cache.leaf_hash_to_index.len();
        if leaf_count != leaf_hash_to_index_cache_length {
            return Err(Error::IntegrityLeafHashToIndexCacheLength(
                leaf_count,
                leaf_hash_to_index_cache_length,
            ));
        }
        let total_count = leaf_count + internal_count + self.block_status_cache.free_index_count();
        let extend_index = self.extend_index();
        if total_count != extend_index.0 as usize {
            return Err(Error::IntegrityTotalNodeCount(extend_index, total_count));
        }
        if !child_to_parent.is_empty() {
            return Err(Error::IntegrityUnmatchedChildParentRelationships(
                child_to_parent.len(),
            ));
        }

        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L1317-1362)
```rust
            deltas::DeltaReaderNode::Internal { left, right } => {
                let index = self.get_new_index();

                let left_index = self.inner_build_blob_from_node_list(
                    nodes,
                    *left,
                    interested_hashes,
                    hashes_and_indexes,
                    all_used_hashes,
                    visited,
                    depth + 1,
                )?;
                let right_index = self.inner_build_blob_from_node_list(
                    nodes,
                    *right,
                    interested_hashes,
                    hashes_and_indexes,
                    all_used_hashes,
                    visited,
                    depth + 1,
                )?;

                for child_index in [left_index, right_index] {
                    self.update_parent(child_index, Some(index))?;
                }
                let block = Block {
                    metadata: NodeMetadata {
                        node_type: NodeType::Internal,
                        dirty: false,
                    },
                    node: Node::Internal(InternalNode {
                        hash: node_hash,
                        parent: Parent(None),
                        left: left_index,
                        right: right_index,
                    }),
                };
                self.insert_entry_to_blob(index, &block)?;

                if interested_hashes.contains(&node_hash) {
                    hashes_and_indexes.push((node_hash, index));
                }
                all_used_hashes.insert(node_hash);

                Ok(index)
            }
```
