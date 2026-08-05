## Analog Identified: `pending_delegator_rewards` in Vote State v4 Is Never Decremented After Block-Reward Distribution — Repeated Double-Spend of the Same Deposited Reward Pool

### Title
Block-revenue-sharing rewards recompute against a `pending_delegator_rewards` balance that is never decremented after payout, allowing the same deposited SOL to be paid to stakers every epoch - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
This is the same bug class as TRST-H-7: a "claimable pool" value (`claimable[gauge]` in SatinVoter) is read to compute a proportional share to pay out, but the code path that performs the payout never subtracts what was just paid from that pool. In Agave's block-revenue-sharing feature (SIMD-0123), the analogous pool is `VoteStateV4::pending_delegator_rewards`, which validators fund via `DepositDelegatorRewards` [1](#0-0) . Each epoch, `calculate_block_reward()` reads this field and computes each stake account's proportional share of it as `block_reward` [2](#0-1) , and that `block_reward` is credited to stake accounts during distribution [3](#0-2) . However, nowhere in the calculation or distribution pipeline is `pending_delegator_rewards` reduced by the amount that was just paid out.

### Finding Description
`add_pending_delegator_rewards()` is the only mutator of this field found in the codebase, and it only increments it (via `DepositDelegatorRewards`) [4](#0-3) . A repository-wide search for any subtraction/reset of `pending_delegator_rewards` (`-=`, `checked_sub`, or reassignment to a reduced value) after distribution turns up nothing outside of test setup code that manually zeroes the field for unrelated `Withdraw` tests [5](#0-4) .

Meanwhile, `calculate_block_reward()` is invoked once per epoch, per stake delegation, during `calculate_stake_rewards_and_commissions()` whenever `feature_snapshot.block_revenue_sharing` is enabled [6](#0-5) . It always uses `vote_state.pending_delegator_rewards()` as the numerator of the proportional split, with no session/epoch-scoped decrement:
```
let pending_delegator_rewards = vote_state.pending_delegator_rewards();
...
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into().unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
``` [2](#0-1) 

The resulting `block_reward` is stored per `PartitionedStakeReward` [7](#0-6)  and, during the distribution phase, is directly credited to the stake account's lamports with `checked_add_lamports` [3](#0-2) . Notably, `distribute_epoch_rewards_in_partition()` explicitly does **not** add `block_reward_lamports_distributed` to `self.capitalization` — only `stake_reward_lamports_minted` (the inflation portion) increases capitalization [8](#0-7) . This confirms the design intent: block-reward lamports are meant to be drawn down from an existing, pre-funded balance (the vote account's `pending_delegator_rewards`/deposited lamports), not newly minted. But since `pending_delegator_rewards` is never decremented, the same deposited balance is treated as fully available again in every subsequent epoch's `calculate_block_reward()` call — exactly mirroring the SatinVoter bug where `claimable[gauge]` was reused across `_distribute()` calls because the reset only happened conditionally.

### Impact Explanation
Because capitalization is not increased for `block_reward_lamports_distributed` (it's assumed to come from existing vote-account lamports), but the corresponding vote-account debit and `pending_delegator_rewards` reset never occur, the protocol effectively re-pays the same reward budget to stakers every epoch that `block_revenue_sharing` is active and stake remains delegated — while the accounting model assumes it's a one-time drawdown. This corrupts the value `pending_delegator_rewards` (never shrinks) and drains real backing (vote account lamports are never actually reduced to fund the repeat payouts, since the field used to compute and gate `Withdraw`/close-account eligibility in `withdraw()` stays permanently non-zero based on stale deposit accounting) [9](#0-8) . This can either (a) cause the bank's capitalization tracking to silently diverge from actual minted/distributed lamports across validators, a consensus-relevant invariant, or (b) allow stakers to receive far more lamports than were ever deposited relative to the accounting model, since the same `pending_delegator_rewards` figure is redistributed epoch after epoch without being consumed.

### Likelihood Explanation
No malicious/privileged actor is required — this triggers under the normal reward-calculation code path whenever the `block_revenue_sharing`, `custom_commission_collector`, and `commission_rate_in_basis_points` features are active and any validator has ever called `DepositDelegatorRewards` (a permissionless instruction any depositor can invoke) [10](#0-9) . Every epoch boundary afterward, `calculate_stake_rewards_and_commissions()` runs unconditionally as part of core reward computation for every bank, guaranteeing repeated exercise of the missing-decrement path with no attacker input needed at all.

### Recommendation
After computing and applying `block_reward` during distribution, subtract the distributed `block_reward` amount from the vote account's `pending_delegator_rewards` (and debit the vote account's lamports accordingly, or explicitly add `block_reward_lamports_distributed` to capitalization if it is intentionally treated as newly minted). This must happen atomically with the stake-account credit in `store_stake_accounts_in_partition`/`build_updated_stake_reward`, similar to how the SatinVoter fix adjusted `claimable[gauge] -= veShare` immediately when `veShare` was computed and consumed.

### Proof of Concept
1. A validator calls `DepositDelegatorRewards` with `deposit = X`, setting `pending_delegator_rewards = X` [11](#0-10) .
2. At epoch N boundary, `calculate_block_reward()` computes `block_reward = X * stake/total_active_stake` for each delegator and it is credited to stake accounts during distribution; `pending_delegator_rewards` remains `X` (unchanged) in the on-chain vote account.
3. At epoch N+1 boundary, `calculate_block_reward()` runs again, reading the still-unchanged `pending_delegator_rewards = X`, and computes and pays out the same (or proportionally recalculated) `block_reward` a second time — with no new deposit having occurred.
4. This repeats every epoch indefinitely, since nothing in `distribution.rs` or `calculation.rs` ever reduces `pending_delegator_rewards`, unlike the "claimable[gauge] -= veShare" fix applied to the analogous SatinVoter bug.

**Uncertainty note:** I was unable to find, within the indexed portion of the codebase, any code path (including in the accounts-db reward-history reconciliation code, feature-gated cleanup routines, or an out-of-index file) that might decrement `pending_delegator_rewards` post-distribution. Given index size limits, it's possible such logic exists in a file not surfaced by search; a Devin session with full repository access should verify this by tracing every write site of `VoteStateV4::pending_delegator_rewards` before treating this as a confirmed, unpatched issue.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L188-231)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L801-833)
```rust
        let block_revenue_sharing = feature_snapshot.block_revenue_sharing;

        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L859-865)
```rust
                            let stake_reward = inflation.stake_reward;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation,
                                    block_reward,
                                }),
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-198)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

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

**File:** programs/vote/src/vote_processor.rs (L5264-5272)
```rust
        // Should fail, can't close vote account when
        // pending_delegator_rewards > 0.
        process_instruction(
            features,
            &serialize(&VoteInstruction::Withdraw(vote_account_lamports)).unwrap(),
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Err(InstructionError::InsufficientFunds),
        );
```

**File:** programs/vote/src/vote_processor.rs (L5304-5311)
```rust
        // Now clear pending delegator rewards.
        {
            let mut vote_state =
                VoteStateV4::deserialize(vote_account.data(), &vote_pubkey).unwrap();
            vote_state.pending_delegator_rewards = 0;
            vote_account.set_data_from_slice(&VoteStateHandler::new_v4(vote_state).serialize());
            vote_account.set_lamports(vote_account_lamports);
        };
```
