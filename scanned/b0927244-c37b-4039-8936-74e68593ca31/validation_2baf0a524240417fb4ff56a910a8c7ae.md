## Analysis

The external report's bug class is: **a queued/pending payout that depends on some external resource (collateral) is left permanently stuck once that resource is fully removed ("sunset"), because the code path that would let it be reclaimed is gated on a state that was designed assuming the resource is still present.**

The closest verified analog in this Agave codebase is in the **vote program's delegator-rewards mechanism (SIMD-0123)**, which introduces a `pending_delegator_rewards` balance held inside a vote account, to be distributed to delegators proportionally to their active stake during partitioned epoch-reward distribution.

### Title
Delegator rewards can become permanently stuck in a vote account once all delegated stake is withdrawn - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`pending_delegator_rewards` lamports are deposited into a vote account via `deposit_delegator_rewards` [1](#0-0)  and are meant to be paid out to delegators proportionally to their active stake through `calculate_block_reward` during epoch-reward distribution [2](#0-1) . If the total active stake delegated to that vote account becomes `0` (e.g., all stakers fully deactivate/withdraw their stake — the vote-account analog of a "sunset collateral") before the pending rewards are distributed, `calculate_block_reward` short-circuits and returns `0` for every stake account, so no distribution ever consumes the pending balance: [3](#0-2) 

Since the distribution path is driven purely by iterating currently-delegated stake accounts, once there are no more delegated stake accounts to iterate, there is no other code path that clears or redistributes `pending_delegator_rewards`.

### Finding Description
`pending_delegator_rewards` is a per-vote-account escrow of lamports owed to delegators, analogous to the report's "queued stables" waiting on collateral redemption. The withdraw/close instruction explicitly protects this value: a vote account cannot be fully closed while `pending_delegator_rewards > 0`, and partial withdrawals must always leave at least `pending_delegator_rewards` lamports behind: [4](#0-3) 

The only mechanism that reduces this balance is the epoch-reward distribution's `calculate_block_reward`, which computes each delegator's share as `pending_delegator_rewards * individual_stake / total_active_stake`. This computation is entirely dependent on `total_active_stake` (the sum of currently delegated, non-withdrawn stake) being non-zero: [5](#0-4) 

If every staker deactivates and withdraws their stake from the vote account before the pending reward is fully distributed (or after it accumulates with zero delegated stake remaining), `total_active_stake` becomes `0`, `calculate_block_reward` returns `0` unconditionally, and the escrowed `pending_delegator_rewards` lamports are never assigned to any delegation and never cleared from the vote account state. Exactly as in the report, the "cooldown"/gating mechanism (here, the `pending_delegator_rewards > 0` check in `withdraw`) that was designed to protect delegators against a premature close now has no counterpart to unwind it once the underlying resource (delegated stake) is gone — there is no equivalent of "set `redemptionCooldownPeriod` to 0 on sunset" for this state.

### Impact Explanation
The lamports backing `pending_delegator_rewards` are real, previously-deposited SOL in the vote account (deposited via CPI in `deposit_delegator_rewards`). Once `total_active_stake` reaches zero with a nonzero `pending_delegator_rewards`, those lamports become permanently unreachable: they cannot be distributed to delegators (there is no active stake to compute a share against) and the authorized withdrawer cannot reclaim them because `withdraw()` unconditionally blocks a full account close while `pending_delegator_rewards > 0`, and also reserves that amount from any partial withdrawal. This is a direct, unprivileged loss of access to already-escrowed funds within a core built-in program (the vote program) and the runtime's epoch-reward-distribution logic.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the specific sequence where delegator rewards are deposited/pending on a vote account, and subsequently all delegated stake is deactivated and withdrawn before the corresponding partitioned-epoch-reward distribution occurs for that stake — plausible for a validator that is being wound down (delegators exiting en masse) while a reward deposit is in flight or freshly accumulated, which is a realistic and unprivileged sequence of ordinary user actions (stake deactivation/withdrawal), not one requiring a malicious actor.

### Recommendation
When `total_active_stake` for a vote account reaches `0` while `pending_delegator_rewards > 0`, either (a) redirect the pending amount to the withdrawer/incinerator so it can be reclaimed, or (b) allow `withdraw()` to reclaim `pending_delegator_rewards` once it is provably undistributable (no remaining active delegations), mirroring the report's recommendation to zero the blocking gate once the underlying resource is gone.

### Proof of Concept
1. Delegator/authorized party calls `deposit_delegator_rewards` on a vote account, setting `pending_delegator_rewards = X` [6](#0-5) .
2. All stakers delegated to that vote account deactivate and fully withdraw their stake before the next partitioned epoch-reward distribution completes for that stake, driving `reward_epoch_delegated_stakes.delegated_stakes` for that vote pubkey to `0` / absent.
3. During distribution, `calculate_block_reward` is invoked with `total_active_stake == 0` and returns `0` for every (now nonexistent) delegation [7](#0-6) ; `pending_delegator_rewards` in the vote account is never decremented.
4. The authorized withdrawer attempts `Withdraw` for the full vote-account balance; `withdraw()` returns `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0` [8](#0-7) , and any partial withdrawal is capped below `pending_delegator_rewards + rent_exempt_minimum` [9](#0-8) , permanently locking `X` lamports in the account.

Note: I was unable to fully trace, within the available tool budget, whether there exists a separate/alternate code path elsewhere in the reward-distribution pipeline that clears `pending_delegator_rewards` independent of `calculate_block_reward`'s active-stake computation; no `checked_sub`/decrement site for this field was found via search. This should be verified directly against the full source before treating the finding as conclusively unmitigated.

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
