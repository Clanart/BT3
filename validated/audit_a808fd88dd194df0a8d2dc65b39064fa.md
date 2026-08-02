[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/loop_summary.rs (L9-11)
```rust
/// Dense index into nodes in the same `LoopSummary`
#[derive(Copy, Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct NodeId(u16);
```

**File:** third_party/move/move-bytecode-verifier/src/loop_summary.rs (L13-14)
```rust
/// Alias to treat vectors as `NodeId -> T` maps.
type NodeMap<T> = Vec<T>;
```

**File:** third_party/move/move-bytecode-verifier/src/loop_summary.rs (L76-90)
```rust
        let num_blocks = cfg.num_blocks() as usize;

        // Fields in LoopSummary that are filled via a depth-first traversal of `cfg`.
        let mut blocks = vec![0; num_blocks];
        let mut descs = vec![0; num_blocks];
        let mut backs = vec![vec![]; num_blocks];
        let mut preds = vec![vec![]; num_blocks];

        let mut next_node = NodeId(0);

        let root_block = cfg.entry_block_id();
        let root_node = next_node.bump();

        let mut exploration = BTreeMap::new();
        blocks[usize::from(root_node)] = root_block;
```
