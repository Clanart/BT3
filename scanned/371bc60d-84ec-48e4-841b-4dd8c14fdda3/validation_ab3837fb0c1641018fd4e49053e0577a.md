Based on my investigation, the strongest local analog to the ACOToken "skip being exercised / can be forced to exhaust the loop" bug class is a state-inconsistency assertion in Agave's partitioned epoch-rewards distribution path, which — unlike the original Solidity bug (a silent "skip") — manifests as a reachable `assert_eq!` panic in bank code that runs on every validator, i.e. a potential consensus halt.

### Title
Staker-controlled stake-account mutation between reward calculation and reward distribution can trigger an `assert_eq!` panic in `build_updated_stake_reward` - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Agave computes and caches per-account stake rewards during a "calculation" phase at the epoch boundary, then applies ("distributes") them lazily over many subsequent blocks/partitions [1](#0-0) . The distribution code assumes the target stake account's on-chain delegation cannot have changed between calculation time and its distribution block, and enforces this with a hard `assert_eq!` rather than a graceful skip [2](#0-1) .

### Finding Description
`store_stake_accounts_in_partition` iterates the pre-computed partition of stake rewards and calls `build_updated_stake_reward` for each stake account, loading the *current* stake state from the live `stakes_cache` [3](#0-2) .

Inside `build_updated_stake_reward`, when the `relax_post_exec_min_balance_check` feature is inactive, the code does not recompute the reward against the live delegation — it instead asserts that the live delegation is exactly consistent with the delegation value snapshotted during the earlier calculation phase: [2](#0-1) 

The comment above `store_stake_accounts_in_partition` makes the underlying (unverified) assumption explicit: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [4](#0-3) 

This is precisely the same broken invariant as in the ACOToken report: the code assumes an account's relevant state is frozen between "recorded at snapshot time" and "acted upon later," and a normal, unprivileged, self-serviceable action by the account owner can invalidate that assumption. A stake account owner is free to call `Split`, `Merge`, `Withdraw`, or otherwise change `delegation.stake` via the Stake program using ordinary instructions signed with their own stake/withdraw authority — there is no evidence in the reward-distribution code path, nor was any interval-based instruction gate found, that blocks such stake-program operations while `Bank::epoch_reward_status` is `Active`/`InsideInterval` (only bookkeeping getters like `get_reward_interval` exist, and no call site couples them to instruction validation was found in the code reachable from this investigation) [5](#0-4) .

If a staker splits/merges their own stake account after the epoch-boundary calculation block but before their account's assigned distribution partition is processed (distribution can span many subsequent blocks, since `partition_indices` covers multiple blocks up to 10% of an epoch's slots) [6](#0-5) , the live `delegation.stake` read from `stakes_cache_accounts` at distribution time will differ from `partitioned_stake_reward.inflation.stake` computed during calculation. The `expected_delegation` (live value + reward) will then not equal `new_stake.delegation.stake` (calc-time value + reward), tripping the `assert_eq!` and panicking the bank-processing thread on every validator that processes that slot.

### Impact Explanation
A `panic!`/`assert_eq!` failure inside `Bank::store_stake_accounts_in_partition`/`build_updated_stake_reward` occurs during block replay and block production — code paths executed by every validator to reach consensus. If the divergence is deterministic (i.e., the same reward-distribution slot and account state is replayed identically by all validators, which it is, since it derives from on-chain state), all validators would panic identically at the same slot, which is a validator-crash/consensus-halt scenario rather than the ACOToken bug's milder "loop always runs out of gas" DoS. This satisfies the "cause consensus halt" / "non-RPC remote exhaustion/crash" impact bar.

### Likelihood Explanation
Likelihood is uncertain because I was unable to conclusively verify (within available tool calls) whether the Stake program actually disallows Split/Merge/Withdraw-type instructions on a delegated stake account while `EpochRewardStatus::Active` is set for the bank. The code comment explicitly states this is assumed to be prevented "by stake-program restrictions," which suggests the reward-distribution code itself does not independently enforce it — it is fully reliant on a guard elsewhere that I could not locate or confirm in the reachable, indexed code (`grep_search` for `EpochRewardsActive`/`reward interval` guards in the stake program yielded no matches). If such a guard exists and is airtight, this finding does not hold; if it does not exist or has gaps (e.g., across the `adjust_delegations_for_rent` feature-gated code path, or for `Split`/`Merge` specifically), the path is trivially reachable by any staker acting only on their own account.

### Recommendation
- Confirm whether the Stake program (or `check_ready_for_deactivation`/similar checks) actually blocks all delegation-mutating instructions for a stake account while `Bank::epoch_reward_status` is active for that epoch, and if not, add such a guard.
- Replace the hard `assert_eq!` in `build_updated_stake_reward` with a recoverable error path (as already exists via `DistributionError`), so a genuine mismatch results in the reward being burned/logged (as the `Err` branch already supports) instead of panicking the bank thread [7](#0-6) [8](#0-7) .
- Add test coverage that explicitly attempts a `Split`/`Merge`/other stake-program mutation on a stake account between the calculation and distribution phases of the same epoch to confirm whether it is rejected or whether it reaches (and panics) `build_updated_stake_reward`.

### Proof of Concept
Conceptual sequence (not independently executed against a live validator due to tool limitations):
1. At the epoch boundary, `calculate_rewards_for_partitioning` snapshots delegation/stake for account `S`, producing `PartitionedStakeReward { stake_pubkey: S, inflation: { stake: X, stake_reward: R, .. }, .. }` [9](#0-8) .
2. Before the block whose partition contains `S` is processed, the owner of `S` submits an ordinary `Split` (or `Merge`) instruction to the Stake program, changing `S`'s on-chain `delegation.stake` to `X' != X`.
3. When `S`'s turn comes up in `store_stake_accounts_in_partition`, `build_updated_stake_reward` loads the live (post-split) `stake.delegation.stake == X'` from `stakes_cache_accounts`, computes `expected_delegation = X' + R`, and compares it against `new_stake.delegation.stake == X + R` (computed from the stale calc-time snapshot) [2](#0-1) .
4. Since `X' != X`, the assertion fails, panicking the bank-processing thread for that slot on every validator that replays/produces it.

**Uncertainty flagged**: I could not locate (or rule out) an explicit stake-program-side guard rejecting stake-account mutations during the active reward interval within the code reachable by my searches; this is the key unverified assumption underpinning likelihood. If such a guard is confirmed to fully cover all mutating stake instructions during the interval, this specific analog does not hold and should be treated as unconfirmed rather than a fully demonstrated vulnerability.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L24-26)
```rust
/// Number of blocks for reward calculation and storing vote accounts.
/// Distributing rewards to stake accounts begins AFTER this many blocks.
const REWARD_CALCULATION_NUM_BLOCKS: u64 = 1;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L534-564)
```rust
    #[derive(Debug, PartialEq, Eq, Copy, Clone)]
    enum RewardInterval {
        /// the slot within the epoch is INSIDE the reward distribution interval
        InsideInterval,
        /// the slot within the epoch is OUTSIDE the reward distribution interval
        OutsideInterval,
    }

    impl Bank {
        /// Return `RewardInterval` enum for current bank
        fn get_reward_interval(&self) -> RewardInterval {
            if matches!(self.epoch_reward_status, EpochRewardStatus::Active(_)) {
                RewardInterval::InsideInterval
            } else {
                RewardInterval::OutsideInterval
            }
        }

        fn is_calculated(&self) -> bool {
            matches!(
                self.epoch_reward_status,
                EpochRewardStatus::Active(EpochRewardPhase::Calculation(_))
            )
        }

        fn is_partitioned(&self) -> bool {
            matches!(
                self.epoch_reward_status,
                EpochRewardStatus::Active(EpochRewardPhase::Distribution(_))
            )
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L29-39)
```rust
#[derive(Serialize, Deserialize, Debug, Error, PartialEq, Eq, Clone)]
enum DistributionError {
    #[error("stake account not found")]
    AccountNotFound,

    #[error("rewards arithmetic overflowed")]
    ArithmeticOverflow,

    #[error("stake account set_state failed")]
    UnableToSetState,
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L284-294)
```rust
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L330-332)
```rust
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-384)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L393-407)
```rust
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L470-481)
```rust
    /// Calculate rewards from previous epoch to prepare for partitioned distribution.
    pub(super) fn calculate_rewards_for_partitioning<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        reward_epoch_delegated_stakes: RewardEpochDelegatedStakes,
        reward_calc_tracer: Option<impl Fn(&RewardCalculationEvent) + Send + Sync>,
        thread_pool: &ThreadPool,
        metrics: &mut RewardsMetrics,
    ) -> PartitionedRewardsCalculation {
```
