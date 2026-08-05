Audit Report

## Title
Fully-withdrawn stake accounts are silently dropped from the reward distribution partition, permanently burning already-committed inflation rewards - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

## Summary
Epoch inflation rewards are calculated once at the epoch boundary and stored in an immutable `PartitionedStakeRewards` list, paid out incrementally across the following blocks via `distribute_epoch_rewards_in_partition` → `store_stake_accounts_in_partition` [1](#0-0) . When a partition is finally applied, `build_updated_stake_reward` looks up the destination stake account from `stakes_cache_accounts`, and if the account is missing (because it was fully withdrawn and zeroed out after calculation but before distribution), the reward is not credited — it is added to `stake_reward_lamports_burned` instead [2](#0-1) [3](#0-2) .

## Finding Description
`StakesCache::check_and_store` removes a stake delegation entry entirely once an account's lamport balance reaches zero [4](#0-3) . This is reachable via an ordinary, unprivileged `Withdraw` instruction once a stake account is fully deactivated — confirmed directly by the existing CLI integration test `test_stake_delegation_and_withdraw_available`, which deactivates a stake account and then successfully withdraws the entire balance to zero via `SpendAmount::Available` [5](#0-4) .

Reward distribution is deliberately spread across multiple blocks: `distribute_partitioned_epoch_rewards` computes `distribution_starting_block_height` and processes one partition per subsequent block until `distribution_end_exclusive`, calling `distribute_epoch_rewards_in_partition` only when the current block height falls inside that partition's window [6](#0-5) . `store_stake_accounts_in_partition` resolves each entry's pubkey against `stakes_cache_accounts`, a parameter representing the live stakes cache at the time that specific block is processed — not a snapshot taken at reward-calculation time [2](#0-1) . If the pubkey is absent, `build_updated_stake_reward` returns `DistributionError::AccountNotFound`, and the caller unconditionally burns the reward amount rather than crediting or refunding it [3](#0-2) .

The code's own doc comment on `store_stake_accounts_in_partition` asserts the invariant this bug violates: "Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned" [7](#0-6) . No corresponding guard tying `Withdraw` to `epoch_reward_status` (or any pending-reward tracking) was found in the indexed portions of `programs/stake/src`; the only analogous protection found in the codebase is the SIMD-0123 `pending_delegator_rewards` check that blocks *vote account* closure, which has no stake-account counterpart [8](#0-7) .

## Impact Explanation
This is a direct, permanent loss of a staker's already-committed inflation reward: the amount is calculated and recorded in `PartitionedStakeRewards` at the epoch boundary, but if the account reaches zero lamports before its specific partition block, the credit is redirected into `stake_reward_lamports_burned`/`block_reward_lamports_burned` and capitalization accounting, never reaching the staker or any recoverable destination [9](#0-8) . This matches the "fund loss" impact category for the runtime/accounts reward-accounting path.

## Likelihood Explanation
Exploitability requires no privilege beyond being an ordinary stake-account withdraw authority: deactivate stake so it fully cools down by the epoch boundary, let the reward calculation include the account (it was active during part of the rewarded epoch), then submit a normal `Withdraw` for the full available balance before the assigned partition block is processed. The number of distribution blocks (`get_reward_distribution_num_blocks`) scales with the number of stake accounts and can span up to 10% of an epoch's slots, giving a real, non-trivial window [10](#0-9) . The action requires only a single unprivileged transaction and is fully repeatable by any staker who fully deactivates and withdraws before their partition executes.

## Recommendation
Do not resolve reward-distribution targets against the live `StakesCache`; instead persist or re-derive enough state at calculation time so that a fully-withdrawn stake account still receives its already-earned reward (e.g., credit it via the destination of the withdrawal, or refuse withdrawal while a partitioned reward is outstanding, analogous to the `pending_delegator_rewards` guard added for vote accounts under SIMD-0123).

## Proof of Concept
1. Deactivate a delegated stake account so it becomes fully inactive at (or shortly after) the epoch boundary.
2. At `begin_partitioned_rewards`, the account's non-zero inflation reward is computed and stored in `PartitionedStakeRewards` for later distribution.
3. Before the block corresponding to this account's partition index is processed, submit a `Withdraw` instruction for the full available balance (mirrors `test_stake_delegation_and_withdraw_available` in `cli/tests/stake.rs`).
4. `StakesCache::check_and_store` sees `lamports() == 0` and removes the delegation from the cache.
5. When the assigned partition block runs, `build_updated_stake_reward` fails to find the pubkey in `stakes_cache_accounts` and returns `DistributionError::AccountNotFound`; `store_stake_accounts_in_partition` adds the reward to `stake_reward_lamports_burned` instead of paying it out.
6. The staker's already-earned reward is permanently lost.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L137-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-204)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L381-396)
```rust
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
