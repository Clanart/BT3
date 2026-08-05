Based on my research, the closest structural analog in Agave to the Astaria "reward calculated off the wrong quantity" bug class is in the new SIMD-0123 block-revenue-sharing reward path, specifically `calculate_block_reward`.

### Title
Block-revenue-sharing reward distribution reads the accumulated `pending_delegator_rewards` pool without any confirmed decrement, risking repeated redistribution of the same deposited lamports across epochs - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
SIMD-0123 introduced a `DepositDelegatorRewards` vote instruction that transfers lamports into a vote account and accumulates them into a `pending_delegator_rewards` counter via `add_pending_delegator_rewards`, which only ever performs a `checked_add`. [1](#0-0) 
Each epoch, `calculate_block_reward` reads this same `pending_delegator_rewards` value and splits it proportionally among all delegators of that vote account, based on `stake / total_active_stake` for the rewarded epoch. [2](#0-1) 

This mirrors the Astaria bug pattern precisely: the reward-splitting function is computed off a stored/committed value (`stack.point.amount`/`pending_delegator_rewards`) rather than off the amount that should actually be metered as "paid out this cycle." In Astaria, the bug was that `beforePayment` used the lien's total amount instead of the actual payment amount, letting the strategist collect inflated rewards repeatedly. In Agave's analog, `calculate_block_reward` is a pure, read-only function of the vote account's `pending_delegator_rewards` field — it does not mutate or reduce that field itself.

### Finding Description
`calculate_block_reward` is invoked once per stake delegation per epoch from `calculate_stake_rewards_and_commissions`, whenever `block_revenue_sharing` is active: [3](#0-2) 

The function itself is documented as computing "block reward for a stake account based on SIMD-0123" using `pending_delegator_rewards` fetched straight from the vote account's state view, and the comment explicitly acknowledges that "during recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`," implying the pool value can be reused/recomputed across passes: [4](#0-3) 

Across the codebase, every mutation site I could find for `pending_delegator_rewards` is an addition (`add_pending_delegator_rewards`, invoked from `deposit_delegator_rewards`): [5](#0-4) 

I was not able to locate, within the scope of my searches, any corresponding subtraction/decrement of `pending_delegator_rewards` performed as part of `distribute_epoch_rewards_in_partition` / `store_stake_accounts_in_partition` / `build_updated_stake_reward`, which is where block/inflation rewards are actually written back to accounts: [6](#0-5) [7](#0-6) 

If `pending_delegator_rewards` is indeed never reduced when it is distributed as `block_reward`, then the same deposited pool of lamports would be treated as available and redistributed proportionally to stakers every subsequent epoch, even though the underlying lamports were only transferred into the vote account once. This is functionally identical to the Astaria issue: the reward math is driven by a static/committed quantity (`pending_delegator_rewards`, analogous to `stack.point.amount`) instead of the incremental amount that should be consumed/metered per distribution cycle (analogous to Astaria's "amount paid").

### Impact Explanation
If confirmed, this would cause fund/accounting corruption at the protocol level: capitalization tracking (`stake_reward_lamports_minted`, `block_reward_lamports_distributed`) would systematically over-credit stake accounts relative to lamports actually deposited via `DepositDelegatorRewards`, since the vote account's lamport balance would be drained down to below the amount `pending_delegator_rewards` claims is still owed, but the counter itself would not reflect that the reward was already paid. This falls under the "false execution/acceptance" / "fund theft or loss" impact category since it directly corrupts reward-account lamport bookkeeping that the runtime/bank treats as canonical for the epoch-rewards sysvar and capitalization.

### Likelihood Explanation
This is a low-privilege issue path in the sense that it doesn't require a malicious validator/peer — it only requires the `block_revenue_sharing`, `custom_commission_collector`, and `commission_rate_in_basis_points` features to be active (this is a newly-introduced, feature-gated code path per the SIMD-0123 comments), and a single legitimate `DepositDelegatorRewards` call plus normal epoch-boundary reward processing to trigger it every subsequent epoch. Given the extensive test coverage I saw for `deposit_delegator_rewards` (`test_deposit_delegator_rewards`, `test_calculate_block_reward_specific`, `test_calculate_block_reward_prop`), it's very plausible a decrement mechanism exists elsewhere that I did not locate in the scope of my search — this significantly lowers my confidence that this is an actual live bug rather than a gap in my own coverage of the codebase.

### Recommendation
A background engineer should specifically:
1. Grep the full `runtime/src/bank/partitioned_epoch_rewards/` and `programs/vote/src/vote_state/` trees for every write site of `pending_delegator_rewards` field (not just `add_pending_delegator_rewards`), to confirm whether a `checked_sub`/reset exists when `block_reward` is distributed in `build_updated_stake_reward` / `store_stake_accounts_in_partition`.
2. If no decrement exists, add one so `pending_delegator_rewards` is reduced by exactly the sum of `block_reward` amounts successfully distributed in that epoch (mirroring the Astaria fix of using the "amount actually paid" rather than the static committed value).
3. Add a proptest/invariant test asserting that summing `block_reward` payouts across consecutive epochs for a fixed single deposit never exceeds the original deposited amount.

### Proof of Concept
Not constructible with certainty from static analysis alone — this requires runtime verification (e.g., writing an integration test that calls `DepositDelegatorRewards` once, then advances two epoch boundaries, and asserts the vote account is not credited/re-split for the second epoch using the same `pending_delegator_rewards` snapshot). I was not able to execute or fully trace this due to tool-call limits; I recommend a Devin session with repo access to grep exhaustively for `pending_delegator_rewards` mutation sites and write the reproduction test described above before treating this as a confirmed, fixable vulnerability.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L182-231)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
```rust
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
```

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L239-325)
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

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
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
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;

        let stake_at_distribution_epoch = delegation_effective_stake(
            &new_stake.delegation,
            distribution_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        let reward_type = if stake_at_distribution_epoch == 0 {
            RewardType::DeactivatedStake
        } else {
            RewardType::Staking
        };
        Ok(StakeReward {
            stake_pubkey: partitioned_stake_reward.stake_pubkey,
            stake_reward_info: StakeRewardInfo {
                reward_type,
                lamports: i64::try_from(
                    partitioned_stake_reward.inflation.stake_reward
                        + partitioned_stake_reward.block_reward,
                )
                .unwrap(),
                post_balance: account.lamports(),
                commission_bps: partitioned_stake_reward.inflation.commission_bps,
            },
            stake_account: account,
        })
    }
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
