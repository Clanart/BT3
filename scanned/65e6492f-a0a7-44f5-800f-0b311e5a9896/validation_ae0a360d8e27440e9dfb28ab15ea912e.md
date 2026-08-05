Based on the investigation, the strongest structural analog to the reported bug (a fallback error path that "burns" value but fails to persist the state needed for a later recovery step) is the error branch in `store_stake_accounts_in_partition` / `build_updated_stake_reward` in the partitioned epoch-rewards distribution code.

### Title
Stake reward silently burned on `AccountNotFound`/state-mutation failure during partitioned reward distribution, with no persisted record for recovery - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
`distribute_epoch_rewards_in_partition` distributes a partition of calculated stake rewards by calling `store_stake_accounts_in_partition`, which iterates over the partition's `PartitionedStakeReward`s and calls `build_updated_stake_reward` for each one [1](#0-0) . If `build_updated_stake_reward` returns an `Err` (e.g. `DistributionError::AccountNotFound` when the stake account is missing from the `StakesCache`, or `UnableToSetState`), the code logs an error and adds the reward amount to `stake_reward_lamports_burned`/`block_reward_lamports_burned` — but the stake account is never updated and no record is written anywhere that would let the affected staker later reclaim that reward [2](#0-1) .

### Finding Description
This mirrors the reported bug class: a two-phase flow (calculate → distribute, analogous to unstake-request → withdraw) where the second phase has a fallback/error branch that consumes/burns the pending value without saving the corresponding bookkeeping entry that the "claim" step depends on.

- In the calculation phase, `begin_partitioned_rewards` computes `stake_rewards` and stores them in `EpochRewardStatus::Active(EpochRewardPhase::Calculation(...))`, later transitioning to `EpochRewardPhase::Distribution` with `partition_indices` [3](#0-2) .
- In the distribution phase, `store_stake_accounts_in_partition` is the terminal step that actually credits lamports to stake accounts via `Self::build_updated_stake_reward` [4](#0-3) .
- The comment on `store_stake_accounts_in_partition` explicitly states the invariant the code relies on: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned"* [5](#0-4) .
- Despite that invariant, the code still contains a live error branch that burns the reward silently: `stake_reward_lamports_burned += stake_reward_amount;` with only a log line, no persisted record, no return of funds to any owner-visible location [6](#0-5) .
- The `err` path is reachable whenever `stakes_cache_accounts.get(&partitioned_stake_reward.stake_pubkey)` fails to find the pubkey (e.g., the stake account was closed/withdrawn between the epoch-boundary calculation snapshot and the later distribution block, which is plausible since distribution spans multiple blocks after calculation) [7](#0-6) .
- Once the partition is processed, `capitalization` is decremented by `block_reward_lamports_burned` (stake reward burns are absorbed into the sysvar/epoch bookkeeping) and the reward is gone — `update_reward_history_in_partition` only records the `updated_stake_rewards` that succeeded, so burned rewards never appear in reward history either [8](#0-7) .

There is no fallback re-queue, no persisted `pending reward` map keyed by the stake pubkey, and once `distribute_partitioned_epoch_rewards` marks the phase `Inactive` after the final partition, `EpochRewardStatus` is discarded entirely, so there is no mechanism to detect or recover this loss [9](#0-8) .

### Impact Explanation
If the `AccountNotFound`/`UnableToSetState` branch is hit for a legitimate staker (e.g., due to a race between account closure and delayed multi-block reward distribution, or any other divergence between the calculation-time `StakesCache` snapshot and the distribution-time `StakesCache`), that staker's earned reward lamports are permanently burned from capitalization with no path to recovery — a direct, unrecoverable loss of user funds enforced entirely by validator-side (non-malicious) code, matching the "fund loss / no recovery" impact class of the original report.

### Likelihood Explanation
The code's own comment states this branch "should never" trigger, implying the developers believe existing invariants (stake accounts checked at calculation time, stake-program restricting mutation) prevent it. However, the branch is defensively coded and still present, meaning the authors themselves did not have full confidence in the invariant holding across the multi-block window between calculation and distribution. I could not fully verify from local code alone whether a legitimate, benign sequence of operations (not requiring a malicious/privileged actor) can currently make the stake account disappear from `stakes_cache_accounts` between calculation and the specific partition's distribution block — this requires deeper tracing of `StakesCache` updates across the reward-calculation-to-distribution block window, which the available snippets do not fully resolve.

### Recommendation
Given the uncertainty about reachability of the `AccountNotFound`/`UnableToSetState` branch under purely benign conditions, and that the team's own comment asserts it "should never" occur, this should be treated as a candidate for further investigation rather than a confirmed exploitable bug. A background engineering session would be needed to trace `StakesCache` mutation paths across the calculation→distribution block range to confirm or refute reachability.

### Proof of Concept
Not constructible from local static analysis alone — reachability of the burn branch depends on runtime `StakesCache` state transitions across multiple blocks, which requires live/test-harness verification beyond what code reading can establish with certainty.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L95-112)
```rust
        if let EpochRewardPhase::Calculation(status) = &status {
            // epoch rewards have not been partitioned yet, so partition them now
            // This should happen only once immediately on the first rewards distribution block, after reward calculation block.
            let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
            let (partition_indices, partition_us) = measure_us!({
                epoch_rewards_hasher::hash_rewards_into_partitions(
                    &status.all_stake_rewards,
                    &epoch_rewards_sysvar.parent_blockhash,
                    epoch_rewards_sysvar.num_partitions as usize,
                )
            });

            // update epoch reward status to distribution phase
            self.set_epoch_reward_status_distribution(
                distribution_starting_block_height,
                Arc::clone(&status.all_stake_rewards),
                partition_indices,
            );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L151-170)
```rust
        if height.saturating_add(1) >= distribution_end_exclusive {
            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("active", 0, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );

            assert!(matches!(
                self.epoch_reward_status,
                EpochRewardStatus::Active(EpochRewardPhase::Distribution(_))
            ));
            self.epoch_reward_status = EpochRewardStatus::Inactive;
            self.set_epoch_rewards_sysvar_to_inactive();
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L226-237)
```rust
    /// insert non-zero stake rewards to self.rewards
    /// Return the number of rewards inserted
    fn update_reward_history_in_partition(&self, stake_rewards: &[StakeReward]) -> usize {
        let mut rewards = self.rewards.write().unwrap();
        rewards.reserve(stake_rewards.len());
        let initial_len = rewards.len();
        stake_rewards
            .iter()
            .filter(|x| x.get_stake_reward() > 0)
            .for_each(|x| rewards.push((x.stake_pubkey, x.stake_reward_info.into())));
        rewards.len().saturating_sub(initial_len)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L248-252)
```rust
    ) -> Result<StakeReward, DistributionError> {
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L330-336)
```rust
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
    fn store_stake_accounts_in_partition(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L366-408)
```rust
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
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
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
        }
```
