[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/repair/repair_weight.rs (L58-76)
```rust
#[derive(Clone)]
pub struct RepairWeight {
    // Map from root -> a subtree rooted at that `root`
    trees: HashMap<Slot, HeaviestSubtreeForkChoice>,
    // Map from root -> pruned subtree
    // In the case of duplicate blocks linking back to a slot which is pruned, it is important to
    // hold onto pruned trees so that we can repair / ancestor hashes repair when necessary. Since
    // the parent slot is pruned these blocks will never be replayed / marked dead, so the existing
    // dead duplicate confirmed pathway will not catch this special case.
    // We manage the size by removing slots < root
    pruned_trees: HashMap<Slot, HeaviestSubtreeForkChoice>,

    // Maps each slot to the root of the tree that contains it
    slot_to_tree: HashMap<Slot, TreeRoot>,
    root: Slot,

    // When Alpenglow is active we no longer need to track pruned trees
    pruned_tree_tracking_enabled: bool,
}
```

**File:** core/src/repair/repair_weight.rs (L108-112)
```rust
        let mut all_subtree_updates: HashMap<TreeRoot, HashMap<Pubkey, Slot>> = HashMap::new();
        for (slot, pubkey_voters) in voters {
            if slot < self.root {
                continue;
            }
```
