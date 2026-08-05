Based on my investigation, I found a strong local analog to the "removed reward struct causes permanent loss of unclaimed rewards" bug class.

### Title
Fully-withdrawn stake accounts are silently dropped from the reward distribution partition, permanently burning already-committed inflation rewards - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`, `runtime/src/stakes.rs`)

### Summary
Agave calculates each staker's epoch inflation reward once, at the epoch boundary, and stores the result in an immutable `PartitionedStakeRewards` list that is paid out incrementally over the following blocks (partitions) of the new epoch [1](#0-0) . When each partition is actually applied, the code looks up the destination stake account not from a snapshot taken at calculation time, but from the *live* `StakesCache` at the moment of distribution [2](#0-1) . If the stake account is no longer present in that cache, the reward is not credited to anyone — it is simply "burned" (dropped from capitalization accounting) [3](#0-2) .

### Finding Description
This mirrors the reported bug class exactly: a value that has already been "committed" (the reward token struct / here, the calculated `StakeReward`) becomes permanently unclaimable once the corresponding entry (the reward-token struct / here, the stake account) is removed from the collection the payout logic depends on.

`StakesCache::check_and_store` removes a stake delegation from the cache entirely as soon as an account's lamport balance reaches zero: [4](#0-3) 
This happens whenever a staker fully withdraws a deactivated stake account — an ordinary, unprivileged, permissionless operation available to any authorized withdrawer via the stake program's `Withdraw` instruction, confirmed to succeed once stake is fully inactive: [5](#0-4) 

Meanwhile, `build_updated_stake_reward` — called from `store_stake_accounts_in_partition`, which runs during the distribution phase, potentially many blocks after the reward was calculated — resolves the pubkey against `stakes_cache_accounts` (a snapshot of the *current* stakes cache, not the one from calculation time) and returns `DistributionError::AccountNotFound` if it's missing: [2](#0-1) 

The caller treats this as an expected-to-never-happen edge case and simply burns the reward instead of crediting it to the staker or refunding it: [3](#0-2) 

Critically, the code's own doc comment states the assumed invariant that is supposed to prevent this:
> "Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned." [6](#0-5) 

I was unable to find any code in the stake program (`programs/stake/src`) that checks `Bank::epoch_reward_status` or otherwise blocks a `Withdraw` instruction while a staker's reward for the immediately preceding epoch is still pending distribution — my searches for such a guard in the stake-program source returned no matches. This suggests the assumed "stake-program restriction" referenced in the comment either does not exist or does not cover this specific window (deactivated stake fully cooled down at the epoch boundary, withdrawn before its corresponding partition is processed).

### Impact Explanation
A staker whose delegation was included in `PartitionedStakeRewards` (i.e., they already earned and had committed to them a non-zero inflation reward for the prior epoch) can lose that reward permanently and irrecoverably if their stake account's balance reaches zero (via a normal `Withdraw`) before the specific partition/block containing their entry is processed. This is a direct loss of funds for the user — analogous to the Olympus SingleSidedLiquidityVault bug where removing a reward-token struct made the user's already-accrued balance permanently unclaimable, except here the "removal" is of the account from the live `StakesCache` rather than of the reward-token metadata from an array.

### Likelihood Explanation
Reward distribution is spread over multiple blocks after the epoch-boundary calculation (`num_partitions`, computed in `get_reward_distribution_num_blocks`), giving a real time window in which a staker who has fully deactivated stake could submit a `Withdraw` transaction and zero out their account before their specific partition is processed [7](#0-6) . This requires no cooperation from validators, no malicious actor, and no special privilege — it is a normal user action (withdrawing fully-deactivated stake) racing against normal protocol reward distribution timing. Because the stake account was still present and staked during the rewarded epoch, the reward computation legitimately includes it, making the loss unexpected from the user's perspective.

### Recommendation
Snapshot/carry forward enough state at calculation time (or re-derive credit eligibility independent of whether the account still exists in the live cache) so that a stake account that is fully withdrawn between calculation and distribution still receives its already-earned reward — e.g., by crediting the reward to the destination of the withdrawal, or by disallowing full withdrawal of a stake account with a still-outstanding partitioned reward (similar to the "claim only" mode recommended in the referenced report, or similar to the `pending_delegator_rewards` guard that Agave itself already implements for vote-account commission withdrawals via SIMD-0123, seen in `programs/vote/src/vote_state/mod.rs`) [8](#0-7) , rather than silently burning the amount.

### Proof of Concept
1. A staker fully deactivates their stake account near the end of an epoch, such that it becomes fully inactive exactly at (or just after) the epoch boundary.
2. At the epoch boundary, `begin_partitioned_rewards`/`calculate_stake_rewards_and_commissions` computes a non-zero inflation `StakeReward` for this stake account (it was active for part of the rewarded epoch) and stores it in `PartitionedStakeRewards`, to be paid out over the next `num_partitions` blocks [9](#0-8) .
3. Before the specific block/partition containing this staker's index is processed, the staker (now with 0 active stake) submits a `Withdraw` instruction to withdraw the entire account balance to zero.
4. `StakesCache::check_and_store` observes `account.lamports() == 0` and calls `remove_stake_delegation`, deleting the entry from the cache [4](#0-3) .
5. When the reward-distribution block for that partition arrives, `store_stake_accounts_in_partition` -> `build_updated_stake_reward` fails to find the pubkey in `stakes_cache_accounts`, returns `DistributionError::AccountNotFound`, and the reward amount is added to `stake_reward_lamports_burned` instead of being paid to the staker [10](#0-9) .
6. The staker's already-earned reward for the prior epoch is permanently lost.

Note: I could not fully verify whether some other mechanism (e.g., transaction scheduling order, priority-fee sequencing, or an undiscovered check elsewhere in the runtime) prevents this exact race in practice; the stake-program instruction handlers themselves (`programs/stake/src`) did not show any check tied to `epoch_reward_status` in my searches, but the index may not include every relevant file. I'd recommend a Devin session with full repo access to trace the exact block-height ordering guarantees and confirm whether the window described is actually reachable in a single epoch's slot schedule.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L139-158)
```rust
pub(crate) struct StartBlockHeightAndRewards {
    /// the block height of the slot at which rewards distribution began
    pub(crate) distribution_starting_block_height: u64,
    /// calculated epoch rewards before partitioning
    pub(crate) all_stake_rewards: Arc<PartitionedStakeRewards>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct StartBlockHeightAndPartitionedRewards {
    /// the block height of the slot at which rewards distribution began
    pub(crate) distribution_starting_block_height: u64,

    /// calculated epoch rewards pending distribution
    pub(crate) all_stake_rewards: Arc<PartitionedStakeRewards>,

    /// indices of calculated epoch rewards per partition, outer Vec is by
    /// partition (one partition per block), inner Vec is the indices for one
    /// partition.
    pub(crate) partition_indices: Vec<Vec<usize>>,
}
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-335)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L366-407)
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
```

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

**File:** cli/tests/stake.rs (L440-477)
```rust
    // Deactivate stake
    config_validator.command = CliCommand::DeactivateStake {
        stake_account_pubkey: stake_keypair.pubkey(),
        stake_authority: 0,
        sign_only: false,
        deactivate_delinquent: false,
        dump_transaction_message: false,
        blockhash_query: BlockhashQuery::default(),
        nonce_account: None,
        nonce_authority: 0,
        memo: None,
        seed: None,
        fee_payer: 0,
        compute_unit_price: None,
    };
    process_command(&config_validator).await.unwrap();

    // Withdraw available stake
    config_validator.signers = vec![&validator_keypair];
    config_validator.command = CliCommand::WithdrawStake {
        stake_account_pubkey: stake_keypair.pubkey(),
        destination_account_pubkey: recipient_pubkey,
        amount: SpendAmount::Available,
        withdraw_authority: 0,
        custodian: None,
        sign_only: false,
        dump_transaction_message: false,
        blockhash_query: BlockhashQuery::Rpc(Source::Cluster),
        nonce_authority: 0,
        nonce_account: None,
        memo: None,
        seed: None,
        fee_payer: 0,
        compute_unit_price: None,
    };
    process_command(&config_validator).await.unwrap();
    // Complete balance is withdrawn because all stake is inactive
    check_balance!(55 * LAMPORTS_PER_SOL, &rpc_client, &recipient_pubkey);
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1092)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-274)
```rust
    pub(in crate::bank) fn begin_partitioned_rewards(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_block_height: u64,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> u64 {
        let RewardCommissionLamportAmounts {
            distributed_lamports,
            distributed_to_incinerator_lamports,
            burned_lamports,
        } = self.distribute_reward_commissions(
            parent_epoch,
            rewards_calculation,
            rewards_metrics,
            thread_pool,
        );

        let slot = self.slot();
        let distribution_starting_block_height =
            self.block_height() + REWARD_CALCULATION_NUM_BLOCKS;

        let PartitionedRewardsCalculation {
            stake_rewards,
            point_value,
            ..
        } = rewards_calculation;

        let stake_rewards = Arc::clone(&stake_rewards.stake_rewards);

        let num_partitions = self.get_reward_distribution_num_blocks(&stake_rewards);
        self.set_epoch_reward_status_calculation(distribution_starting_block_height, stake_rewards);
```
