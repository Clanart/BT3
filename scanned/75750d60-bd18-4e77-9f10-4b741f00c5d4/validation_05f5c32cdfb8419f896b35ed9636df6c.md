## Title
Reward escrow (`pending_delegator_rewards`) can be permanently burned instead of paid, with no recovery path for the affected delegator — (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

## Summary
The Foundation report's root cause is a generic pattern: funds are moved into an escrow accounting slot tied to a fixed recipient, and if the payout to that specific recipient fails, the escrowed funds become unrecoverable by that recipient (only the original, fixed withdrawal path exists). The closest analog in Agave is the vote program's delegator-rewards escrow introduced by SIMD-0123: block-revenue lamports are deposited into a vote account and tracked in `pending_delegator_rewards` [1](#0-0) , and later "released" per-delegator via `build_updated_stake_reward`/`store_stake_accounts_in_partition` during epoch reward distribution [2](#0-1) . If the specific stake account cannot be found/updated at distribution time, the reward is burned rather than delivered, with no mechanism for the affected delegator to later reclaim it.

## Finding Description
`deposit_delegator_rewards` transfers lamports into a vote account and increments `pending_delegator_rewards`, representing an on-chain liability owed to delegators [3](#0-2) . `withdraw` correctly reserves this liability against the vote account's own withdrawer, preventing the withdrawer from draining below `pending_delegator_rewards` and blocking account closure while it is non-zero [4](#0-3) . That guard protects the pool as a whole, but it does not guarantee any single delegator's *individual* share is deliverable.

At distribution time, `build_updated_stake_reward` looks up each stake account in the stakes cache by pubkey; if the account is not found (`DistributionError::AccountNotFound`), the caller `store_stake_accounts_in_partition` simply logs and adds the amount to `stake_reward_lamports_burned` / `block_reward_lamports_burned` instead of the delegator's balance [5](#0-4) . The lamports have already been drawn down from `pending_delegator_rewards` at calculation time (`calculate_block_reward` computes each delegator's proportional share of the pool and that share is consumed) [6](#0-5) , so once a share is allocated to a delegator that cannot be paid, it is gone — burned — with no `withdrawTo`-style fallback and no re-attribution back to `pending_delegator_rewards` for a later retry. This mirrors the C4 finding's core defect: a fixed, single-shot payout path with no alternate recipient or reclaim mechanism when the intended destination cannot accept the transfer.

## Impact Explanation
Where the Foundation bug locked ETH belonging to a user in escrow (recoverable later, at least in principle, via contract upgrade), the Agave analog is worse in one respect: the lamports are not merely locked, they are explicitly burned (`stake_reward_lamports_burned`/`block_reward_lamports_burned`), i.e. permanently destroyed from that delegator's perspective, with no on-chain state retaining the fact that a specific delegator was shortchanged. This is a fund-loss condition for the affected delegator, though it is bounded to the reward amount for one distribution cycle for accounts that vanish from the stakes cache between reward calculation and the partitioned distribution window (e.g., stake account deactivated/closed by its owner in that window, a legitimate, permissionless, non-malicious action by the delegator themselves).

## Likelihood Explanation
This requires no malicious validator, peer, or privileged actor — it is a timing/ordering effect between the calculation epoch boundary and the later partitioned distribution slots that Agave already implements for epoch rewards, combined with the stake account being removed from `stakes_cache` (e.g., a normal stake withdrawal/closure) in that window. Given partitioned rewards intentionally span many slots after calculation, the window during which a stake account's state can change out from under an already-calculated reward is non-trivial, making this a realistic, low-privilege occurrence rather than a purely theoretical one. I was not able to fully trace whether an existing safeguard elsewhere (e.g., re-crediting the burned amount back into the vote account's `pending_delegator_rewards`, or a follow-up epoch reallocation) closes this gap — the code visible here only records the burn counters and moves on.

## Recommendation
On `DistributionError::AccountNotFound` (and other per-account failures) in `store_stake_accounts_in_partition`, re-credit the failed amount back into the vote account's `pending_delegator_rewards` instead of unconditionally burning it, so it can be retried in a subsequent distribution or reclaimed through an explicit recovery path, analogous to how the Foundation team ultimately chose to migrate escrowed funds into a durable, user-owned balance rather than leave them behind a single fixed payout function.

## Proof of Concept
Conceptual trace (based on the referenced code paths, not independently executed):
1. Delegator's stake is delegated to a vote account; block revenue is deposited via `deposit_delegator_rewards`, incrementing `pending_delegator_rewards` [3](#0-2) .
2. At epoch boundary, `calculate_block_reward` computes this delegator's proportional share of `pending_delegator_rewards` based on stake at that time [7](#0-6) , allocating a `PartitionedStakeReward` for a future distribution slot.
3. Before the scheduled partition slot for that reward is processed, the delegator's stake account pubkey is removed from `stakes_cache` (e.g. stake fully withdrawn/closed).
4. When `store_stake_accounts_in_partition` reaches that index, `build_updated_stake_reward` returns `DistributionError::AccountNotFound` [8](#0-7) , and the caller adds the reward to the burned totals instead of paying anyone [9](#0-8) .
5. The lamports are permanently lost; the delegator's already-allocated share is unrecoverable, with only a debug log entry (`test_build_updated_stake_reward` exercises this exact `AccountNotFound` path in tests) [10](#0-9) .

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L935-988)
```rust
/// Deposit delegator rewards into a vote account (SIMD-0123).
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1122)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-423)
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
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
        DistributionResults {
            stake_reward_lamports_minted,
            stake_reward_lamports_burned,
            block_reward_lamports_distributed,
            block_reward_lamports_burned,
            updated_stake_rewards,
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
}
```
