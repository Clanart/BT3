## Title
`pending_delegator_rewards` block-revenue accounting never decrements the vote account's reserved balance after distribution, permanently locking delegator funds and desynchronizing bank capitalization - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`, `programs/vote/src/vote_state/handler.rs`)

## Summary
This mirrors the Vault.vy "bad debt not subtracted from total_debt_amount" bug class: a liability/reserve value is credited in one place (`pending_delegator_rewards`) and consumed/paid out in another (`block_reward` distribution to stake accounts), but the source-of-truth reserve field is never decremented to reflect that payout. The result is a permanent accounting mismatch that both locks delegator funds inside the vote account and desynchronizes `Bank::capitalization` from the real sum of lamports.

## Finding Description
`deposit_delegator_rewards` is the only code path that mutates `pending_delegator_rewards`, and it only ever increases it via `add_pending_delegator_rewards`: [1](#0-0) [2](#0-1) 

`withdraw()` treats `pending_delegator_rewards` as a hard reserve that can never be pulled out by the authorized withdrawer, on top of the rent-exempt minimum: [3](#0-2) 

At epoch-reward time, `calculate_block_reward` reads this same `pending_delegator_rewards` value from the vote account and uses it (capped) to compute a per-delegator `block_reward` share: [4](#0-3) 

That `block_reward` is then added directly to the *stake* account's lamports in `build_updated_stake_reward`: [5](#0-4) 

and summed into `block_reward_lamports_distributed` in `store_stake_accounts_in_partition`: [6](#0-5) 

Finally, `distribute_epoch_rewards_in_partition` only adjusts `self.capitalization` for `stake_reward_lamports_minted` (added) and `block_reward_lamports_burned` (subtracted) — it never adds `block_reward_lamports_distributed` to capitalization, and nowhere is the vote account's lamports balance or its `pending_delegator_rewards` field decremented by that same amount: [7](#0-6) 

This is structurally identical to the Sherlock report: `bad_debt` (here, `pending_delegator_rewards`) is used to determine a payout that is credited elsewhere (LP repayment / stake-account lamports), but the mapping that represents the reserved liability (`total_debt_amount` / `pending_delegator_rewards`) is never reduced by the amount already paid out.

## Impact Explanation
Two compounding effects follow directly from the missing decrement, using only local code evidence:
1. **Locked delegator funds**: `withdraw()`'s reserve check (`min_balance = rent_exempt + pending_delegator_rewards`) never shrinks even after the same lamports have already been paid out as `block_reward` to stake accounts. The authorized withdrawer of the vote account can never reclaim that already-distributed amount — the funds become permanently unreachable, exactly like the LPs in the referenced report who could not withdraw the amount already "repaid" as bad debt.
2. **Capitalization desync**: `distribute_epoch_rewards_in_partition` adds `stake_reward_lamports_minted` (inflation) to `capitalization`, but does not add `block_reward_lamports_distributed`. Since `build_updated_stake_reward` unconditionally increases the target stake account's lamports by `block_reward` with no corresponding decrease anywhere in the bank, the real sum of all lamports in the ledger grows by `block_reward_lamports_distributed` every distribution cycle while `self.capitalization` (the bank's tracked total) does not, in this codepath, account for that growth. This is a false accounting invariant (`capitalization == sum of all account lamports`) which the runtime relies on for validation (e.g., snapshot/capitalization checks). This falls under the disclosed impact classes of "wrong accounting/false execution" and, over time, "material loss of funds" for the affected vote-account withdrawer, matching the accepted severity rationale in the referenced report.

## Likelihood Explanation
This path is only reachable once several features that are still gated are activated together (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing`, SIMD-0123 / Vote State V4) — confirmed by the feature checks in `deposit_delegator_rewards`'s caller and in `calculate_stake_rewards_and_commissions`'s `block_revenue_sharing` flag: [8](#0-7) [9](#0-8) 

Given that these are not yet fully active mainnet features (SIMD-0123 related), I could not fully verify from the indexed code whether a later, un-indexed reconciliation step (e.g., in `redeem_delegation_rewards` or `update_reward_history_in_partition`) decrements `pending_delegator_rewards`/withdraws the corresponding lamports from the vote account, since my searches for any subtraction of `pending_delegator_rewards` returned no matches anywhere in the indexed codebase. This absence is the core evidence for the finding, but the index has size limits, so a full-repository confirmation is warranted.

## Recommendation
When distributing `block_reward` sourced from `pending_delegator_rewards`, the vote account's lamports balance and its `pending_delegator_rewards` field must be decremented by the exact `block_reward` amount paid to each delegator's stake account in the same distribution step (analogous to repaying the full liability, not just the "received" portion, in the referenced Vault.vy fix). Additionally, `distribute_epoch_rewards_in_partition` should reconcile `capitalization` for `block_reward_lamports_distributed` transfers so that the sum of all account lamports remains consistent with the tracked bank capitalization.

## Proof of Concept
Conceptual sequence based on the local code paths cited above:
1. A block producer calls `DepositDelegatorRewards` on a v4 vote account, incrementing `pending_delegator_rewards` by `D` lamports and transferring `D` lamports into the vote account (`deposit_delegator_rewards`).
2. At epoch boundary, with `block_revenue_sharing` active, `calculate_block_reward` computes each delegator's proportional share of `D` (capped at `pending_delegator_rewards`).
3. `store_stake_accounts_in_partition` / `build_updated_stake_reward` adds each computed `block_reward` directly to the corresponding stake account's lamports.
4. `distribute_epoch_rewards_in_partition` updates `capitalization` only for `stake_reward_lamports_minted` and `block_reward_lamports_burned` — the `D` lamports newly credited to stake accounts are never subtracted from the vote account's lamports or `pending_delegator_rewards` field, and never reflected as a capitalization increase.
5. The vote account's `withdraw()` reserve check still treats the full original `D` as reserved (`pending_delegator_rewards` unchanged), so the withdrawer can never access the vote account's remaining balance up to `D`, even though `D` was already fully paid to delegators — the funds are permanently stranded, and `Bank::capitalization` no longer equals the true sum of ledger lamports.

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

**File:** programs/vote/src/vote_state/mod.rs (L974-987)
```rust
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1121)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-231)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L173-204)
```rust
    /// Process reward credits for a partition of rewards
    /// Store the rewards to AccountsDB, update reward history record and total capitalization.
    fn distribute_epoch_rewards_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) {
        let pre_capitalization = self.capitalization();
        let (
            DistributionResults {
                stake_reward_lamports_minted,
                stake_reward_lamports_burned,
                block_reward_lamports_distributed,
                block_reward_lamports_burned,
                updated_stake_rewards,
            },
            store_stake_accounts_us,
        ) = measure_us!(self.store_stake_accounts_in_partition(partition_rewards, partition_index));

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L393-398)
```rust
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
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
