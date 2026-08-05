Based on my research, I found a concrete and significant gap in the SIMD-0123 block-revenue-sharing reward path that mirrors the reported bug class (funds moved into a pool that is then paid out using stale/uncoordinated accounting).

### Title
Block-reward distribution mints lamports into stake accounts without debiting the vote account's deposited pool or decrementing `pending_delegator_rewards` - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
`DepositDelegatorRewards` allows anyone to transfer lamports into a vote account and atomically increments `VoteStateV4::pending_delegator_rewards` in the same instruction [1](#0-0) . Once per epoch, `calculate_block_reward` reads this `pending_delegator_rewards` value from a frozen snapshot of the vote account and computes each delegator's share proportional to `stake / total_active_stake` [2](#0-1) . That computed `block_reward` is later added directly to each stake account's lamport balance in `build_updated_stake_reward` [3](#0-2) .

### Finding Description
In every code path I could inspect, the lamports credited to stake accounts as `block_reward` are never subtracted from the vote account's actual lamport balance, nor is `vote_state.pending_delegator_rewards` ever decremented to reflect that the pot has already been "spent" on that epoch's distribution. The comment on `calculate_block_reward` even acknowledges awareness of double-crediting risk ("if stake account has already received rewards, it's possible to have stake > total_active_stake... pending_delegator_rewards could overflow") but only clamps the *per-delegator* payout to `pending_delegator_rewards`, never touches the aggregate pool state itself [4](#0-3) . The withdraw() guard in the vote program reserves `pending_delegator_rewards` as an un-withdrawable minimum balance forever, based purely on this monotonically-increasing counter [5](#0-4) , which is consistent with the value never being reduced after distribution.

This is the same root-cause shape as the BMX report: a value that gates/derives a reward payout (`tokensPerInterval` in BMX; `pending_delegator_rewards` here) is not kept in sync with the underlying pool once a distribution consumes it, so each new epoch's distribution re-reads a pool figure that already includes funds attributed to a prior distribution. Concretely, the same `pending_delegator_rewards` figure (net of new deposits) keeps being used as the numerator for `total_active_stake`-weighted rewards epoch after epoch, so recipients across multiple epochs can be paid out of the *same* deposited amount repeatedly, while the corresponding lamports simultaneously remain locked in the vote account (since `withdraw` still reserves them) — the stake-account credit side is effectively minted new capitalization rather than transferred from the vote account's balance.

### Impact Explanation
If confirmed at the point of block/edge cases, this would be an unprivileged fund-duplication bug in the reward/inflation accounting path (validated by unprivileged `DepositDelegatorRewards` callers and unprivileged stake delegators), causing incorrect capitalization growth and/or repeated payout of the same pooled rewards across epochs — a "fund theft/incorrect issuance" class impact in the runtime rewards subsystem.

### Likelihood Explanation
Medium-to-uncertain. I was not able to fully trace, within the available search iterations, whether `distribute_reward_commissions`/`store_stake_accounts_in_partition` deducts `block_reward_lamports_distributed` from the depositing vote account's lamports or decrements `pending_delegator_rewards` somewhere I did not locate (e.g., in `fee_distribution.rs`, which does reference `block_revenue_collector`/`checked_sub_lamports` matches I could not inspect in time) [6](#0-5) . This is a real gap in my verification, not a confirmed exploit — the reviewed reward-distribution code (`build_updated_stake_reward`, `store_stake_accounts_in_partition`) only shows lamports being *added* to stake accounts [7](#0-6)  with no corresponding debit visible in the snippets I retrieved.

### Recommendation
A Devin agent with full repository access should:
1. Trace every call site in `runtime/src/bank/fee_distribution.rs` and `runtime/src/bank/partitioned_epoch_rewards/*` that touches `pending_delegator_rewards` or the vote account's lamport balance after `calculate_block_reward` is invoked, to determine definitively whether the deposited pool is decremented/consumed when `block_reward` is paid out.
2. If no such decrement/debit exists, add logic to subtract distributed `block_reward` amounts from `pending_delegator_rewards` (and debit the vote account's lamports correspondingly, or explicitly account for them in `Bank::capitalization`) at the same point the value is calculated, before it can be spent again in a subsequent epoch or recalculation (`recalculate_stake_rewards`).
3. Add a proptest/unit test analogous to `test_delegation_adjustment_at_distribution` that runs two consecutive epochs of block-reward distribution from the same `DepositDelegatorRewards` deposit and asserts the pot cannot be paid out twice.

### Proof of Concept
Given the incomplete trace, I cannot provide a fully verified PoC. The suspected repro sequence, pending confirmation of the missing decrement, would be:
1. Call `VoteInstruction::DepositDelegatorRewards` to deposit `N` lamports into a vote account, setting `pending_delegator_rewards = N` [8](#0-7) .
2. Let epoch `E` boundary snapshot this vote account; `calculate_block_reward` distributes up to `N` lamports to stakers of that epoch, without visibly reducing `pending_delegator_rewards` [2](#0-1) .
3. If `pending_delegator_rewards` remains `N` (unconfirmed), the same `N` would again act as the numerator pool in epoch `E+1`'s `calculate_block_reward`, producing a second payout from the same nominal deposit.

Given the uncertainty on step 2/3, this should be treated as a lead requiring direct code confirmation, not a fully verified vulnerability.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L183-231)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-297)
```rust
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
```

**File:** runtime/src/bank/fee_distribution.rs (L1-1)
```rust
use {
```
