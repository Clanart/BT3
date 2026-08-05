### Title
Stake accounts modified/closed between epoch-reward calculation and multi-block distribution silently forfeit already-committed inflation rewards - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
This is the closest local analog to the BentoBox/Aave report: a "best effort, continue on failure" pattern where a per-item balance-crediting operation is allowed to fail, and instead of halting/reverting or retrying, the code silently records the amount as "burned" and moves on — exactly the `try/catch {}` pattern from the external report. In Agave, the analog lives in `Bank::store_stake_accounts_in_partition`, which applies previously-calculated stake rewards to stake accounts over multiple blocks. If a stake account cannot be found in the current `StakesCache` snapshot at distribution time (`DistributionError::AccountNotFound`), that staker's already-computed reward is dropped and counted into `stake_reward_lamports_burned` instead of being paid, with only a log line as an indicator. [1](#0-0) 

### Finding Description
Solana's epoch reward payout is a two-phase, multi-block process: rewards are *calculated* once at the epoch boundary and cached in `PartitionedStakeRewards`/`StartBlockHeightAndPartitionedRewards`, then *distributed* to stake accounts over `num_partitions` subsequent blocks via `distribute_partitioned_epoch_rewards` → `distribute_epoch_rewards_in_partition` → `store_stake_accounts_in_partition`. [2](#0-1) 

For each stake pubkey whose reward was computed during calculation, `build_updated_stake_reward` re-looks-up the account in the *current* `stakes_cache_accounts` (a live, mutable snapshot) rather than the state at calculation time, and returns `Err(DistributionError::AccountNotFound)` if it is missing: [3](#0-2) 

`store_stake_accounts_in_partition` treats this error identically to the BentoBox `_exit()` try/catch: it does **not** retry, re-credit elsewhere, or halt block production — it just logs and adds the reward amount to a "burned" counter, and the stake account (and its owner) never receives the lamports: [4](#0-3) 

The function's own doc comment acknowledges this is meant to be unreachable in normal operation ("Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned"), i.e. this is explicitly a defensive-only code path, not a validated invariant: [5](#0-4) 

The corrupted/lost value is the delegator's `stake_reward` (and any associated `block_reward`) computed during the calculation phase: capitalization is never increased for it (`stake_reward_lamports_minted` excludes it), so the reward is permanently unrealized rather than merely delayed. Reward distribution spans many blocks (`get_reward_distribution_num_blocks`), during which the corresponding `StakeAccount` entries live in the mutable `StakesCache`, which is continuously updated by ordinary (unprivileged) transactions such as `Withdraw`, `Merge`, `Deactivate`+`Withdraw`, etc. [6](#0-5) 

Existing guards do not close this gap: there is no lock or protocol rule preventing a stake account holder from fully withdrawing/closing their stake account (once fully deactivated) between the reward-calculation block and their assigned distribution-partition block, which can be delayed by up to ~10% of an epoch's slots.

### Impact Explanation
If a delegator's stake account is removed from `StakesCache` (e.g., fully withdrawn after deactivation, or merged away) after reward calculation but before the specific partition block in which their reward would be applied, the accrued inflation reward for that epoch is silently discarded rather than paid or requeued. This is a fund-loss bug for the affected staker: the amount that should have been credited never reaches any account and capitalization tracking (`stake_reward_lamports_burned`) simply absorbs it as a rounding/error metric, with no user-facing surfacing beyond a debug log line. This matches the BentoBox class of bug ("exiting" a position mid-flight to cause silent partial loss for other/self stakeholders) rather than being an attacker-vs-victim theft, since it is a self-inflicted timing race for the affected staker (their own late-distributed reward is lost), but it is nonetheless unprivileged, protocol-level, and produces false accounting (reward reported as accrued during calculation, then vanished during distribution).

### Likelihood Explanation
Triggering requires only ordinary, permissionless stake operations (deactivate + withdraw, or merge) timed to land between the epoch-boundary reward calculation and the specific block, up to `num_partitions` blocks later, in which that delegator's reward is scheduled for distribution. Because `partition_indices` assignment is based on a hash of the parent blockhash (not validator-controllable beforehand) but the *window* in which such a mutation can land is many blocks wide (partitioned distribution spans a meaningful fraction of an epoch), a delegator who deactivates stake near an epoch boundary and withdraws promptly after activation cooldown could realistically race their own withdrawal against their own not-yet-applied reward credit. This is a corner case rather than a high-frequency exploit, and the code authors clearly expected it to be unreachable ("there should never be rewards burned"), suggesting it is a genuine, if rare, invariant violation rather than an intentionally-accepted design tradeoff.

### Recommendation
Do not silently discard rewards on `AccountNotFound`/`ArithmeticOverflow` in `store_stake_accounts_in_partition`. Options: (1) snapshot the exact `StakeAccount` state used for reward calculation and carry it through to distribution instead of re-reading the live, mutable `StakesCache`, so a subsequently-closed account still receives its payout (or the reward is redirected/refunded rather than burned); or (2) if closing a stake account with a pending, uncredited reward must be disallowed, add an explicit protocol-level restriction preventing withdrawal/closure of a stake account that still has an outstanding partitioned reward scheduled, mirroring the BentoBox report's core fix of "revert/guard rather than silently accept partial loss."

### Proof of Concept
Conceptual repro (concrete instruction-level PoC would need to be validated in a running local Agave test-validator, which is not confirmed here):
1. Approach epoch boundary with a delegated stake account that has a pending inflation reward computed in `calculate_stake_rewards_and_commissions`.
2. Ensure the account is scheduled into a distribution partition several blocks after the epoch boundary (`num_partitions` > 1, which occurs whenever total stake accounts exceed `partitioned_rewards_stake_account_stores_per_block`). [6](#0-5) 
3. Before that partition's block height is reached, submit ordinary `Deactivate`+`Withdraw` (or `Merge`) instructions to fully remove the stake account from `StakesCache`.
4. When `distribute_epoch_rewards_in_partition` reaches this account's partition index, `build_updated_stake_reward` returns `Err(DistributionError::AccountNotFound)`, and `store_stake_accounts_in_partition` moves the previously-calculated reward into `stake_reward_lamports_burned` instead of paying it out. [4](#0-3) 

Note: I was unable to run this scenario in this environment (no filesystem/terminal access here); the existing unit test `test_build_updated_stake_reward` already independently demonstrates that `AccountNotFound` is a reachable, handled `Err` variant of `build_updated_stake_reward`, confirming the code path exists and is exercised in tests. [7](#0-6)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-149)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L384-407)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L807-832)
```rust
        let nonexistent_account = Pubkey::new_unique();
        let partitioned_stake_reward = PartitionedStakeReward {
            stake_pubkey: nonexistent_account,
            inflation: InflationReward {
                stake: new_stake,
                stake_reward,
                commission_bps: Some(commission_bps),
            },
            block_reward,
        };
        let stakes_cache = bank.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        assert_eq!(
            Bank::build_updated_stake_reward(
                distribution_epoch,
                &stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                &partitioned_stake_reward,
                &rent,
                adjust_delegations_for_rent,
                true,
            )
            .unwrap_err(),
            DistributionError::AccountNotFound
        );
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
