[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** ledger/src/shred/merkle_tree.rs (L47-68)
```rust
    pub fn try_new_with_len(
        shreds: impl Iterator<Item = Result<Hash, Error>>,
        len: usize,
    ) -> Result<MerkleTree, Error> {
        let capacity = get_merkle_tree_size(len);
        let mut nodes = Vec::with_capacity(capacity);
        for shred in shreds {
            nodes.push(shred?);
        }
        let init = (len > 1).then_some(len);
        for size in successors(init, |&k| (k > 2).then_some((k + 1) >> 1)) {
            let offset = nodes.len() - size;
            for index in (offset..offset + size).step_by(2) {
                let node = &nodes[index];
                let other = &nodes[(index + 1).min(offset + size - 1)];
                let parent = join_nodes(node, other);
                nodes.push(parent);
            }
        }
        debug_assert_eq!(nodes.len(), capacity);
        Ok(MerkleTree { nodes })
    }
```

**File:** merkle-tree/src/merkle_tree.rs (L112-150)
```rust
    pub fn new<T: AsRef<[u8]>>(items: &[T]) -> Self {
        let cap = MerkleTree::calculate_vec_capacity(items.len());
        let mut mt = MerkleTree {
            leaf_count: items.len(),
            nodes: Vec::with_capacity(cap),
        };

        for item in items {
            let item = item.as_ref();
            let hash = hash_leaf!(item);
            mt.nodes.push(hash);
        }

        let mut level_len = MerkleTree::next_level_len(items.len());
        let mut level_start = items.len();
        let mut prev_level_len = items.len();
        let mut prev_level_start = 0;
        while level_len > 0 {
            for i in 0..level_len {
                let prev_level_idx = 2 * i;
                let lsib = &mt.nodes[prev_level_start + prev_level_idx];
                let rsib = if prev_level_idx + 1 < prev_level_len {
                    &mt.nodes[prev_level_start + prev_level_idx + 1]
                } else {
                    // Duplicate last entry if the level length is odd
                    &mt.nodes[prev_level_start + prev_level_idx]
                };

                let hash = hash_intermediate!(lsib, rsib);
                mt.nodes.push(hash);
            }
            prev_level_start = level_start;
            prev_level_len = level_len;
            level_start += level_len;
            level_len = MerkleTree::next_level_len(level_len);
        }

        mt
    }
```

**File:** ledger/src/blockstore.rs (L1986-1998)
```rust
            // Add parent info as the last leaf. The `fec_set_count` is bound
            // into this leaf so that an adversary cannot convince a verifier
            // that the count is off-by-one when the number of FEC sets is
            // even (which makes the total leaf count odd and causes the last
            // leaf to be hashed with itself during tree construction).
            .chain(std::iter::once(Ok(hashv(&[
                &parent_slot.to_le_bytes(),
                parent_block_id.as_ref(),
                &fec_set_count.to_le_bytes(),
            ]))));

        MerkleTree::try_new_with_len(merkle_tree_leaves, fec_set_count as usize + 1)
            .map_err(|_| BlockstoreError::MerkleTreeConstructionFailure(slot, location))
```
