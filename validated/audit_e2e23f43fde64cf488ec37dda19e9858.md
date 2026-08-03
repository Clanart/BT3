[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** storage/scratchpad/src/sparse_merkle/updater.rs (L26-30)
```rust
type Result<T> = std::result::Result<T, UpdateError>;

type InMemSubTree = super::node::SubTree;
type InMemInternal = InternalNode;

```

**File:** storage/scratchpad/src/sparse_merkle/updater.rs (L300-339)
```rust
    pub(crate) fn update(
        root: InMemSubTree,
        updates: &'a [(K, Option<V>)],
        proof_reader: &'a impl ProofRead,
        generation: u64,
    ) -> Result<InMemSubTree> {
        let updater = Self {
            depth: 0,
            info: SubTreeInfo::from_in_mem(&root, generation),
            updates,
            generation,
        };
        Ok(updater.run(proof_reader)?.into_subtree())
    }

    fn run(self, proof_reader: &impl ProofRead) -> Result<InMemSubTreeInfo> {
        // Limit total tasks that are potentially sent to other threads.
        const MAX_PARALLELIZABLE_DEPTH: usize = 8;
        // No point to introduce Rayon overhead if work is small.
        const MIN_PARALLELIZABLE_SIZE: usize = 2;

        let generation = self.generation;
        let depth = self.depth;
        match self.maybe_end_recursion()? {
            MaybeEndRecursion::End(ended) => Ok(ended),
            MaybeEndRecursion::Continue(myself) => {
                let (left, right) = myself.into_children(proof_reader)?;
                let (left_ret, right_ret) = if depth <= MAX_PARALLELIZABLE_DEPTH
                    && left.updates.len() >= MIN_PARALLELIZABLE_SIZE
                    && right.updates.len() >= MIN_PARALLELIZABLE_SIZE
                {
                    POOL.join(|| left.run(proof_reader), || right.run(proof_reader))
                } else {
                    (left.run(proof_reader), right.run(proof_reader))
                };

                Ok(InMemSubTreeInfo::combine(left_ret?, right_ret?, generation))
            },
        }
    }
```

**File:** storage/scratchpad/src/sparse_merkle/node.rs (L66-87)
```rust
impl Node {
    pub fn new_leaf(key: HashValue, value: HashValue, generation: u64) -> Self {
        Self {
            generation,
            inner: NodeInner::Leaf(LeafNode::new(key, value)),
        }
    }

    #[cfg(test)]
    pub fn new_internal(left: SubTree, right: SubTree, generation: u64) -> Self {
        Self {
            generation,
            inner: NodeInner::Internal(InternalNode { left, right }),
        }
    }

    pub fn new_internal_from_node(node: InternalNode, generation: u64) -> Self {
        Self {
            generation,
            inner: NodeInner::Internal(node),
        }
    }
```
