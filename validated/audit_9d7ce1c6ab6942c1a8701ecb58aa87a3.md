### Title
Permissionless `DepositDelegatorRewards` griefing permanently blocks vote-account closure/withdrawal for un-delegated vote accounts - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
`VoteInstruction::DepositDelegatorRewards` (SIMD-0123) lets *any* signer transfer an arbitrary (even 1-lamport) amount into *any* vote account, unconditionally incrementing that vote account's `pending_delegator_rewards` field. [1](#0-0)  The `withdraw` instruction then refuses to close the vote account (or withdraw below the reserved amount) as long as `pending_delegator_rewards > 0`. [2](#0-1)  The only code path that reduces this pending amount is the per-epoch block-reward calculation, which is driven by stake accounts *delegated to that vote account* and explicitly returns `0` when the vote account has no active delegated stake. [3](#0-2)  Consequently, an attacker can grief a vote account that currently has little or no delegated stake (e.g., a validator winding down and trying to reclaim rent by closing its vote account) by depositing a trivial amount via `DepositDelegatorRewards`, permanently setting `pending_delegator_rewards > 0` with no delegated stake left to ever consume/clear it.

### Finding Description
- `deposit_delegator_rewards` requires only that the *source* account sign; it does not require any relationship to the vote account's authorized voter/withdrawer, so it is fully unprivileged/permissionless. [4](#0-3) 
- The deposited amount is added to `pending_delegator_rewards` via `checked_add`, with no minimum threshold and no cap tied to actual expected delegator earnings. [5](#0-4) 
- `withdraw()` treats `pending_delegator_rewards` as an untouchable reserve: full closure (`remaining_balance == 0`) is rejected outright if `pending_delegator_rewards > 0`, and partial withdrawals must leave at least `rent_exempt_minimum + pending_delegator_rewards` in the account. [6](#0-5) 
- The only mechanism that reduces `pending_delegator_rewards` is `calculate_block_reward`, invoked once per epoch per stake delegation pointing at the vote account; if `total_active_stake` (from `reward_epoch_delegated_stakes`) for that vote account is `0`, the function returns `0` and nothing is redeemed. [7](#0-6)  No stake delegations exist for a vote account with zero delegated stake, so the redemption loop simply never iterates over it, leaving `pending_delegator_rewards` unchanged indefinitely.
- This directly parallels the reported bug class: a trivial-cost, permissionless external action (transferring a small amount) forcibly puts a shared resource into a state that blocks the legitimate owner's withdrawal, with no bound on the resulting lock duration and no self-service escape hatch for the victim.

### Impact Explanation
An attacker can pay a negligible fee (1 lamport deposit + tx fee) to permanently prevent a validator operator from closing their vote account and reclaiming the rent-exempt reserve, as long as the vote account has no (or insufficient) active delegated stake to ever trigger reward redemption. This is an unprivileged fund-lock/DoS against a specific validator's vote account state, achievable at negligible cost and repeatable against any vote account the attacker chooses, including ones that never had a chance to accrue stake yet.

### Likelihood Explanation
High: the instruction is permissionless, cheap, and requires no special conditions beyond `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing` features being active (all gated as SIMD activations, not permission checks). [8](#0-7)  Any vote account temporarily or permanently without delegated stake (new vote accounts, validators mid-way through deactivating all stake, or ones being wound down) is a viable target.

### Recommendation
- Require that `DepositDelegatorRewards` only accept deposits up to a value bounded by an actual expected reward computation (or require the depositor to be an authorized party / the runtime itself during reward distribution), rather than allowing arbitrary unprivileged deposits.
- Alternatively, allow the authorized withdrawer to reclaim/burn/redistribute a stranded `pending_delegator_rewards` balance when the vote account has zero active delegated stake, so the reserve cannot become permanently unspendable.

### Proof of Concept
1. Attacker calls `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` against a target vote account that currently has zero effective delegated stake, using any signer account as the source (per `deposit_delegator_rewards` at `programs/vote/src/vote_state/mod.rs:936-988`).
2. This increments `pending_delegator_rewards` to `1` via `add_pending_delegator_rewards` (`programs/vote/src/vote_state/handler.rs:196-209`).
3. Since the vote account has no delegated stake, `calculate_block_reward` is never invoked for it (no stake delegations reference it), so `pending_delegator_rewards` is never reduced (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-231`).
4. The authorized withdrawer subsequently calls `withdraw()` to fully close the account and reclaim rent; the call fails with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0` (`programs/vote/src/vote_state/mod.rs:1084-1122`), and this failure persists indefinitely absent stake being delegated to the account.

Note: I was unable to locate any additional code path elsewhere in the indexed codebase that clears/redistributes `pending_delegator_rewards` outside of the epoch reward distribution flow described above; if such a path exists outside the indexed portions of the repo, it would need to be verified directly in a full checkout.

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
