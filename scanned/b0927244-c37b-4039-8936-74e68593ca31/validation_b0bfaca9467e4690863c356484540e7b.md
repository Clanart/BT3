### Title
Delegator reward pool (`pending_delegator_rewards`) is distributed by current stake share, not by stake duration, letting a last-minute staker capture rewards accumulated by prior delegators - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Solana's block-revenue-sharing mechanism (SIMD-0123) lets anyone deposit lamports into a vote account's `pending_delegator_rewards` pool via `deposit_delegator_rewards` [1](#0-0) , and this balance accumulates in the vote account with no per-epoch or time-weighted accounting - it is just a single running counter incremented over an arbitrary, possibly long, span of epochs [2](#0-1) . When rewards are distributed, `calculate_block_reward` splits the *entire current* `pending_delegator_rewards` balance among delegators purely by their stake's share of `total_active_stake` in the rewarded epoch [3](#0-2) . Unlike inflation rewards, which require a stake to have actually observed/earned vote credits during the period (`calc_earned_credits`) [4](#0-3) , the block-reward split has no such time-weighting - only the generic one-epoch activation delay applies (`activation_epoch == rewarded_epoch` ⇒ zero reward) [5](#0-4) . This is structurally identical to the NonUSTStrategy bug: a shared value pool built up by prior participants is misattributed at payout time to whoever holds the largest current share, rather than to those whose stake generated/backed it.

### Finding Description
`deposit_delegator_rewards` allows arbitrary lamports (from block-revenue commission sharing) to be added to `VoteStateV4::pending_delegator_rewards` at any time [6](#0-5) . This value is not epoch-scoped; it can grow across many epochs while a validator has few or no active delegators (e.g., a validator that just started collecting block revenue but has low current stake).

When the reward-distribution pass runs, `calculate_block_reward` computes each delegation's share purely as:

```
stake_effective_at_rewarded_epoch * pending_delegator_rewards / total_active_stake_at_rewarded_epoch
``` [7](#0-6) 

This function is called unconditionally for every stake delegation when `block_revenue_sharing` is enabled, with no check on how long the pending rewards took to accumulate, nor on how long the stake has actually been delegated beyond the minimal one-epoch warm-up [8](#0-7) . The only guard against instant-stake abuse is the generic "just activated" rule that zeroes points for a stake whose `activation_epoch == rewarded_epoch` [9](#0-8) , but this rule only delays capture by a single epoch - it does not require the staker's exposure duration to match the duration over which `pending_delegator_rewards` was built up.

Consequently, a large staker can:
1. Identify a vote account whose `pending_delegator_rewards` has grown large over many epochs relative to its (small) currently delegated stake.
2. Delegate a large stake to that vote account and wait exactly one epoch for activation.
3. At the next reward distribution, receive `large_stake / total_active_stake * pending_delegator_rewards` - a disproportionate slice of a pool that existing, long-term delegators actually "earned" through sustained exposure to that validator.
4. Deactivate and withdraw immediately afterward.

This mirrors the report's root cause exactly: the cost/benefit of a shared pool (swap fee pool in the report; delegator reward pool here) is not attributed to the party whose activity/duration generated it, but is split by an instantaneous ownership snapshot that a well-capitalized actor can game.

### Impact Explanation
Existing long-term delegators of a vote account effectively subsidize a newcomer who staked for only one warm-up epoch, losing a portion of the block-revenue rewards that should have accrued to them. This is a fund-misattribution/fund-loss issue among unprivileged delegators, not requiring any malicious validator or trusted-role behavior - it is available to any staker with sufficient capital, and the target vote account can be entirely honest.

### Likelihood Explanation
The attack requires: (a) a vote account with `block_revenue_sharing`/SIMD-0123 active and a non-trivial `pending_delegator_rewards` balance relative to its current `total_active_stake`, and (b) the attacker having enough SOL to temporarily dominate that stake pool for one warm-up epoch plus the distribution epoch. Both conditions can occur naturally (e.g., early in a validator's block-revenue-sharing lifecycle, or after most delegators unstake, leaving a large pending pool relative to remaining stake). No special privileges, malicious nodes, or front-running are needed - this is a straightforward capital-weighted opportunistic delegation, comparable in spirit to the liquidity-pool-dominance move described in the original report.

### Recommendation
Track how much of `pending_delegator_rewards` accrued while each stake was actually active (e.g., snapshot/checkpoint the pool per epoch and only allow a stake to claim from the portion accrued during its active epochs), or require a minimum holding period proportional to the payout being claimed, similar to how inflation rewards require `calc_earned_credits` to reflect the actual epoch(s) the vote credits were observed. At minimum, prevent a single reward distribution from paying out disproportionately from a multi-epoch-accumulated pool to stake that was only active for the statutory minimum warm-up period.

### Proof of Concept
1. Enable `custom_commission_collector`, `commission_rate_in_basis_points`, and `block_revenue_sharing` features (SIMD-0123/0185/0232/0291).
2. Create vote account `V` with a small amount of currently delegated stake (e.g., 1 SOL from delegator `A`), and let `deposit_delegator_rewards` accumulate a large `pending_delegator_rewards` balance over several epochs via `DepositDelegatorRewards` instructions [10](#0-9) .
3. Attacker `B` creates a new stake account delegating a very large amount (e.g., 1,000,000 SOL) to `V` and waits one epoch for `activation_epoch != rewarded_epoch`.
4. At the next reward distribution, `calculate_block_reward` computes `B`'s share as `stake_B / (stake_A + stake_B) * pending_delegator_rewards`, awarding `B` almost all of the pool that had accumulated while only `A` was staked [7](#0-6) , matching the unit-test harness pattern in `get_block_reward_for_test` which demonstrates this exact stake-ratio calculation [11](#0-10) .
5. `B` deactivates and withdraws, having captured value contributed to the pool over epochs during which `B` held no stake at all, at `A`'s expense.

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

**File:** programs/vote/src/vote_state/handler.rs (L190-209)
```rust
    pub(crate) fn pending_delegator_rewards(&self) -> u64 {
        match &self.target_state {
            TargetVoteState::V4(v4) => v4.pending_delegator_rewards,
        }
    }

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L815-833)
```rust
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4246-4318)
```rust
    fn get_block_reward_for_test(
        individual_stake: u64,
        total_stake: u64,
        pending_delegator_rewards: u64,
        rewarded_epoch: u64,
    ) -> u64 {
        let voter_pubkey = Pubkey::new_unique();
        let vote_account = {
            let identity = Keypair::new();
            let bls_keypair =
                BLSKeypair::derive_from_signer(&identity, BLS_KEYPAIR_DERIVE_SEED).unwrap();
            let (bls_pubkey, bls_pop) = create_bls_proof_of_possession(&voter_pubkey, &bls_keypair);
            let vote_init = VoteInitV2 {
                node_pubkey: identity.pubkey(),
                authorized_voter: identity.pubkey(),
                authorized_voter_bls_pubkey: bls_pubkey,
                authorized_voter_bls_proof_of_possession: bls_pop,
                ..VoteInitV2::default()
            };
            let mut vote_state = VoteStateV4::new(
                &vote_init,
                &voter_pubkey,
                &identity.pubkey(),
                &Clock::default(),
            );
            vote_state.pending_delegator_rewards = pending_delegator_rewards;
            let mut account = solana_account::AccountSharedData::new(
                1_000_000_000,
                VoteStateV4::size_of(),
                &solana_vote_program::id(),
            );
            account
                .serialize_data(&VoteStateVersions::new_v4(vote_state))
                .unwrap();
            VoteAccount::try_from(account).unwrap()
        };

        let vote_accounts = [(voter_pubkey, (total_stake, vote_account))]
            .into_iter()
            .collect();
        let ag_epoch_type = AlpenglowEpochType::Alpenglow {
            migration_epoch: 0,
            reward_epoch_delegated_stakes: RewardEpochDelegatedStakes {
                epoch: rewarded_epoch,
                delegated_stakes: [(voter_pubkey, total_stake)].into_iter().collect(),
            },
        };

        let delegation = Delegation {
            voter_pubkey,
            stake: individual_stake,
            activation_epoch: u64::MAX, // boostrap stake so it's fully active
            ..Default::default()
        };

        let mut stake_history = StakeHistory::default();
        for epoch in 0..=rewarded_epoch {
            stake_history.add(epoch, StakeHistoryEntry::with_effective(total_stake));
        }

        let use_fixed_point_stake_math = true;
        let new_warmup_cooldown_rate_epoch = Some(0);

        calculate_block_reward(
            rewarded_epoch,
            &delegation,
            &stake_history,
            &vote_accounts,
            &ag_epoch_type,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        )
    }
```

**File:** runtime/src/inflation_rewards/points.rs (L158-181)
```rust
fn calc_earned_credits(
    stake: &Stake,
    final_epoch_credits: u64,
    initial_epoch_credits: u64,
    new_credits_observed: &mut u64,
) -> u128 {
    let credits_in_stake = stake.credits_observed;

    // figure out how much this stake has seen that
    //   for which the vote account has a record
    let earned_credits = if credits_in_stake < initial_epoch_credits {
        // the staker observed the entire epoch
        final_epoch_credits - initial_epoch_credits
    } else if credits_in_stake < final_epoch_credits {
        // the staker registered sometime during the epoch, partial credit
        final_epoch_credits - *new_credits_observed
    } else {
        // the staker has already observed or been redeemed this epoch
        //  or was activated after this epoch
        0
    };
    *new_credits_observed = (*new_credits_observed).max(final_epoch_credits);
    u128::from(earned_credits)
}
```

**File:** runtime/src/inflation_rewards/mod.rs (L236-249)
```rust
    // Drive credits_observed forward unconditionally when rewards are disabled
    // or when this is the stake's activation epoch
    if point_value.rewards == 0 {
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&SkippedReason::DisabledInflation.into());
        }
        force_credits_update_with_skipped_reward = true;
    } else if stake.delegation.activation_epoch == rewarded_epoch {
        // not assert!()-ed; but points should be zero
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&SkippedReason::JustActivated.into());
        }
        force_credits_update_with_skipped_reward = true;
    }
```

**File:** programs/vote/src/vote_processor.rs (L409-426)
```rust
        VoteInstruction::DepositDelegatorRewards { deposit } => {
            // SIMD-0123: Deposit delegator rewards.
            // Requires:
            // * SIMD-0185: Vote State V4
            // * SIMD-0291: Commission in Basis Points
            // * SIMD-0232: Custom Commission Collector
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points
                || !feature_set.custom_commission_collector
                || !feature_set.block_revenue_sharing
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(2)?;
            drop(me);
            vote_state::deposit_delegator_rewards(invoke_context, 0, 1, deposit, &signers)
        }
```
