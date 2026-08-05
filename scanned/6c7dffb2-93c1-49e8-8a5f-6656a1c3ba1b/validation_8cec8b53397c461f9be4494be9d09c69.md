[1](#0-0) [2](#0-1)

### Citations

**File:** accounts-db/src/partitioned_rewards.rs (L23-36)
```rust
/// Convenient constant for default partitioned epoch rewards configuration
/// used for benchmarks and tests.
pub const DEFAULT_PARTITIONED_EPOCH_REWARDS_CONFIG: PartitionedEpochRewardsConfig =
    PartitionedEpochRewardsConfig {
        stake_account_stores_per_block: MAX_PARTITIONED_REWARDS_PER_BLOCK,
    };

impl Default for PartitionedEpochRewardsConfig {
    fn default() -> Self {
        Self {
            stake_account_stores_per_block: MAX_PARTITIONED_REWARDS_PER_BLOCK,
        }
    }
}
```

**File:** accounts-db/src/partitioned_rewards.rs (L38-46)
```rust
impl PartitionedEpochRewardsConfig {
    /// Constructs a config with an explicit 400ms-slot baseline for tests and
    /// benchmarks.
    pub fn new_for_test(stake_account_stores_per_block: u64) -> Self {
        Self {
            stake_account_stores_per_block,
        }
    }
}
```
