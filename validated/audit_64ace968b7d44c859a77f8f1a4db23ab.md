[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/commitment_service.rs (L38-52)
```rust
impl TowerCommitmentAggregationData {
    pub fn new(
        bank: Arc<Bank>,
        root: Slot,
        total_stake: Stake,
        node_vote_state: (Pubkey, TowerVoteState),
    ) -> Self {
        Self {
            bank,
            root,
            total_stake,
            node_vote_state,
        }
    }
}
```

**File:** core/src/commitment_service.rs (L54-64)
```rust
fn get_highest_super_majority_root(mut rooted_stake: Vec<(Slot, u64)>, total_stake: u64) -> Slot {
    rooted_stake.sort_by(|a, b| a.0.cmp(&b.0).reverse());
    let mut stake_sum = 0;
    for (root, stake) in rooted_stake {
        stake_sum += stake;
        if (stake_sum as f64 / total_stake as f64) > VOTE_THRESHOLD_SIZE {
            return root;
        }
    }
    0
}
```
