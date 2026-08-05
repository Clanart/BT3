## Analog Found

### Title
Partitioned epoch-reward distribution silently burns a staker's calculated reward when `build_updated_stake_reward` fails, instead of aborting/retrying the payout - (File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs)

### Summary
The external report describes a pattern where an internal, fallible sub-operation (`pullReward`) is wrapped in a try/catch; when it fails, the failure is swallowed, an error event is emitted, and the caller silently proceeds as if the reward were zero, permanently losing the funds that should have gone to the staker. Agave's partitioned epoch-reward distribution path contains a structurally identical pattern: `store_stake_accounts_in_partition` calls `Bank::build_updated_stake_reward` for every stake account scheduled to receive a reward in that partition/block, and on `Err(_)` it does not abort or retry - it converts the already-computed reward amount into "burned" lamports and moves on, exactly like the swallow-and-continue behavior flagged in the report.

### Finding Description
`Bank::build_updated_stake_reward` returns a `Result<StakeReward, DistributionError>` with three failure variants (`AccountNotFound`, `ArithmeticOverflow`, `UnableToSetState`): [1](#0-0) 

The reward amount for each stake account is computed earlier during the **calculation phase** (`calculate_stake_rewards_and_commissions`) and is fixed inside `PartitionedStakeReward`. Distribution of these already-computed rewards happens later, spread over multiple blocks (`REWARD_CALCULATION_NUM_BLOCKS` and beyond), during the **distribution phase**: [2](#0-1) 

When `store_stake_accounts_in_partition` processes a partition, it calls `build_updated_stake_reward` for each entry and, on error, silently converts the calculated reward into "burned" lamports rather than reverting/retrying the payout, logging only an `error!` line: [3](#0-2) 

This is the exact analog of the reported bug class: a fallible operation whose failure is caught, converted into a "no-op"/loss outcome, and the surrounding process (here, the whole per-partition batch of hundreds/thousands of other stakers) continues unaffected — the individual staker whose entry failed just loses the reward, with no way to retry, and the code comment itself acknowledges this is not supposed to happen but is handled defensively: [4](#0-3) 

The `AccountNotFound` branch is reachable whenever the stake account present in `stakes_cache_accounts` at distribution time no longer matches the pubkey recorded during calculation — this happens if the stake account is closed/withdrawn to zero, or deactivated and fully withdrawn, in the window between the epoch-boundary calculation block and its assigned distribution block (a window that can span many blocks depending on `get_reward_distribution_num_blocks`): [5](#0-4) 

Unlike the Solidity `try/catch` pattern, Solana's per-instruction execution is atomic, so this exact bug class cannot occur inside a single user transaction; however, `distribute_partitioned_epoch_rewards` is bank-level, cross-block validator logic, not a reverting instruction — so the "swallow error, zero the reward, continue" pattern from the report reproduces faithfully here.

### Impact Explanation
If triggered, the calculated stake reward for the affected staker is converted into `stake_reward_lamports_burned` / `block_reward_lamports_burned` and is never credited to any account — it is removed from the reward budget and the staker permanently loses SOL rewards they were already promised (i.e., already subtracted from the total distributable pool in the epoch-boundary calculation). This matches the report's core impact: "stakers... lose all earned rewards" due to a swallowed internal failure rather than the whole operation failing safely.

### Likelihood Explanation
I was **not able to confirm or refute** the code comment's claim that "further state mutation [is] prevented by stake-program restrictions" during the distribution window — I could not locate, within the indexed portion of the codebase, an explicit check in the stake program that blocks withdraw/deactivate/close instructions on a stake account while `EpochRewards` sysvar is active (I did find such an explicit block for the *vote* program's `withdraw` function guarding `pending_delegator_rewards`, but found no equivalent for the stake program in the indexed code). Without confirming that guard exists and is airtight for every state-mutating stake instruction (withdraw, deactivate, split, merge, set-lockup, etc.) across the full multi-block distribution window, I cannot assert this path is definitely reachable by an ordinary staker action — this is the key open uncertainty. If such a guard exists and is comprehensive, likelihood is low (defense-in-depth only); if any state-mutating stake path is missed, likelihood becomes plausible since it only requires an ordinary user transaction (no malicious/privileged actor needed).

### Recommendation
- Confirm (or add) an explicit, comprehensive block on stake-account-closing/mutating instructions in the stake program for any account that has an already-calculated, not-yet-distributed reward (i.e., while `EpochRewards.active == true` and the account still has a pending partitioned entry), so `AccountNotFound`/`UnableToSetState` in `build_updated_stake_reward` truly become unreachable rather than merely "should never happen."
- As defense-in-depth, consider treating any occurrence of `DistributionError` as a "should never happen" invariant violation (panic/halt in debug or emit a critical, non-suppressible alert) rather than silently burning the reward, so operators are forced to investigate rather than losing lamports unnoticed. This mirrors the report's short-term recommendation ("revert instead of silently swallowing") adapted to Agave's non-transactional, cross-block context.

### Proof of Concept
Concrete reproduction was **not verified** due to the open uncertainty above about stake program guards. Conceptually (pending confirmation that no guard exists):
1. Wait for a stake account to be included in a `PartitionedStakeReward` during `calculate_stake_rewards_and_commissions` at an epoch boundary (calculation phase).
2. Before that account's assigned partition block arrives (distribution can span dozens of blocks), submit a stake-program instruction that empties/removes the stake delegation for that pubkey from `StakesCache` (e.g., full withdrawal after deactivation), if permitted.
3. When `store_stake_accounts_in_partition` processes that account's partition, `stakes_cache_accounts.get(&pubkey)` returns `None`, `build_updated_stake_reward` returns `Err(DistributionError::AccountNotFound)`, and the previously-computed reward is added to `stake_reward_lamports_burned` instead of being paid — the staker receives nothing for that epoch's reward despite it having been calculated and reserved. [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-171)
```rust
impl Bank {
    /// Process reward distribution for the block if it is inside reward interval.
    pub(in crate::bank) fn distribute_partitioned_epoch_rewards(&mut self) {
        let EpochRewardStatus::Active(status) = &self.epoch_reward_status else {
            return;
        };

        let distribution_starting_block_height = match &status {
            EpochRewardPhase::Calculation(status) => status.distribution_starting_block_height,
            EpochRewardPhase::Distribution(status) => status.distribution_starting_block_height,
        };

        let height = self.block_height();
        if height < distribution_starting_block_height {
            return;
        }

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

            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("partition_us", partition_us, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );
        }

        let EpochRewardStatus::Active(EpochRewardPhase::Distribution(partition_rewards)) =
            &self.epoch_reward_status
        else {
            // We should never get here.
            unreachable!(
                "epoch rewards status is not in distribution phase, but we are trying to \
                 distribute rewards"
            );
        };

        let distribution_end_exclusive =
            distribution_starting_block_height + partition_rewards.partition_indices.len() as u64;

        assert!(
            self.epoch_schedule.get_slots_in_epoch(self.epoch)
                > partition_rewards.partition_indices.len() as u64
        );

        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-336)
```rust
    /// Store stake rewards in partition
    /// Returns DistributionResults containing the sum of all the rewards
    /// stored, the sum of all rewards burned, and the updated StakeRewards.
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
    fn store_stake_accounts_in_partition(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L384-408)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L403-428)
```rust
    /// # stake accounts to store in one block during partitioned reward interval
    pub(super) fn partitioned_rewards_stake_account_stores_per_block(&self) -> u64 {
        self.partitioned_rewards_stake_account_stores_per_block
    }

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
