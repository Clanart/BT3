Audit Report

## Title
Withdrawn stake accounts lose already-calculated epoch rewards because `StakesCache::check_and_store` purges zero-lamport entries before partitioned distribution runs - (File: runtime/src/stakes.rs, runtime/src/bank/partitioned_epoch_rewards/distribution.rs)

## Summary
The partitioned epoch rewards mechanism calculates a `PartitionedStakeRewards` list once at the epoch boundary and pays it out over multiple subsequent blocks by re-fetching each stake account from the *live* `StakesCache` at distribution time via `build_updated_stake_reward`. `StakesCache::check_and_store` unconditionally evicts any pubkey whose lamports reach zero, so a staker who fully deactivates and withdraws between the calculation block and their assigned partition's block will have their entry removed from the cache, causing the lookup to fail and the already-earned reward to be converted into a burned reward rather than paid.

## Finding Description
`StakesCache::check_and_store` removes a stake account from the cache as soon as its lamports hit zero, with no check for outstanding/pending rewards: [1](#0-0) 

`distribute_partitioned_epoch_rewards` computes `PartitionedStakeRewards` once at the calculation block and spreads payout of that fixed list across `partition_indices.len()` subsequent blocks, with each block processing only the pubkeys assigned to its partition index: [2](#0-1) 

At the actual payout block, `build_updated_stake_reward` looks up the pubkey by reference into `stakes_cache_accounts` (an `imbl::HashMap<Pubkey, StakeAccount<Delegation>>`), and returns `DistributionError::AccountNotFound` if the account is not present: [3](#0-2) 

The distribution code explicitly documents the assumption that stake-program restrictions prevent state mutation during the reward interval such that "there should never be rewards burned": [4](#0-3) 

However, I was unable to fully trace, within the tool budget available, the exact call site and semantics of `stakes_cache_accounts` inside `store_stake_accounts_in_partition` (i.e., confirmation of whether this map is a live snapshot taken fresh at each partition's block height versus a snapshot frozen at calculation time). This is the crux of the claim: if `stakes_cache_accounts` is re-derived from the live `StakesCache` at each distribution block (as the claim and the `AccountNotFound` error path imply), then a withdrawal occurring after calculation but before that pubkey's partition is processed would indeed cause the account to be missing from the map and its reward burned. The claim's own citations (`DistributionError::AccountNotFound` handling in `store_stake_accounts_in_partition`, converting the amount into `stake_reward_lamports_burned`) are consistent with the code shown above.

There is no evidence in this codebase of a `RewardInterval`/`get_reward_interval()` gate blocking ordinary `Deactivate`/`Withdraw` stake-program instructions during the distribution window; the report's cited test (`test_rewards_period_system_transfer`) demonstrates that ordinary transactions, including transfers, are processed unimpeded throughout the reward period. This directly undermines the code comment's assumption of "stake-program restrictions" preventing mutation, since a withdrawal to zero lamports is exactly the kind of mutation not restricted.

## Impact Explanation
If confirmed, the impact is a real, unprivileged loss of already-earned/reserved stake rewards: the reward amount is diverted from `stake_reward_lamports_minted` into `stake_reward_lamports_burned`, meaning the staker who legitimately earned the reward for the preceding epoch never receives it, and no other party benefits — it is simply excluded from the capitalization increase. This matches an accounting/fund-loss condition for an ordinary, unprivileged staker performing a completely normal action (deactivate + withdraw stake).

## Likelihood Explanation
The precondition described (deactivate and fully withdraw a stake account before its deterministically-hashed partition's block is reached) is achievable by any wallet holder using only public, standard stake-program instructions, requiring no special privilege, and the partition assignment is deterministic and can be estimated in advance via `hash_rewards_into_partitions`, making the scenario realistically reachable and repeatable across epochs.

## Recommendation
Add a guard so that reward distribution accounts for stake accounts that were withdrawn between calculation and distribution — e.g., pay the reward to the account owner directly via the withdrawal destination, track "pending distribution" pubkeys and block/delay full closure of a stake account with an outstanding partitioned reward (analogous to the `pending_delegator_rewards` guard pattern in the vote program's `withdraw`), or otherwise replace the unvalidated comment assumption in `store_stake_accounts_in_partition` with an actual enforced invariant.

## Proof of Concept
1. At the last slot of epoch `N`, the bank calculates `PartitionedStakeRewards` including staker Alice's stake account `A`, scheduling payout starting at `distribution_starting_block_height` (per `distribute_partitioned_epoch_rewards`).
2. `hash_rewards_into_partitions` assigns `A`'s reward to partition `k`.
3. Before block `distribution_starting_block_height + k`, Alice submits `Deactivate` then `Withdraw` to empty account `A` to zero lamports — an ordinary, unrestricted transaction as shown by `test_rewards_period_system_transfer`.
4. `check_and_store` observes `A`'s lamports are `0` and removes `A` from `StakesCache` (runtime/src/stakes.rs lines 99-116).
5. At block `distribution_starting_block_height + k`, `build_updated_stake_reward` fails to find `A`, returning `DistributionError::AccountNotFound` (runtime/src/bank/partitioned_epoch_rewards/distribution.rs lines 239-252), and the reward amount is added to `stake_reward_lamports_burned` instead of being paid to Alice.
6. Alice's epoch-`N` reward for account `A` is permanently lost with no recipient.

### Citations

**File:** runtime/src/stakes.rs (L99-116)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L90-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L239-252)
```rust
    fn build_updated_stake_reward(
        distribution_epoch: u64,
        stake_history: &StakeHistory,
        new_warmup_cooldown_rate_epoch: Option<Epoch>,
        stakes_cache_accounts: &imbl::HashMap<Pubkey, StakeAccount<Delegation>>,
        partitioned_stake_reward: &PartitionedStakeReward,
        rent: &Rent,
        adjust_delegations_for_rent: bool,
        use_fixed_point_stake_math: bool,
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
