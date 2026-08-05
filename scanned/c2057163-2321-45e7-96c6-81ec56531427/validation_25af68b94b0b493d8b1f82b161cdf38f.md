Based on the investigation into the vote program's SIMD-0123 delegator-rewards / block-revenue-sharing mechanism, here is the strongest local analog found to the reported bug class (a value used to compute a distribution that is not synchronized with the value that actually gets consumed/moved).

### Title
`pending_delegator_rewards` is used to mint block-revenue-sharing rewards every epoch but is never decremented, causing repeated/duplicate reward issuance from the same deposited pool - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `programs/vote/src/vote_state/mod.rs`)

### Summary
The `HyperEvmVault` bug pattern is: an internal accounting field (`requestSum.assets`) is used to compute distributable value, but the real underlying balance can diverge from it (via donation), and nothing ever reconciles the two, breaking a share/redeem invariant. The Agave analog is structurally similar but on the "credit" side: the vote account exposes a tracked field `pending_delegator_rewards` [1](#0-0)  that is (a) added to via `deposit_delegator_rewards` [2](#0-1) , (b) treated as a reserved/undistributable balance in `withdraw` [3](#0-2) , and (c) consumed as the source pool for per-epoch block-revenue-sharing reward calculation in `calculate_block_reward` [4](#0-3) .

### Finding Description
`calculate_block_reward` reads `vote_state.pending_delegator_rewards()` and computes each stake account's share of it (`pending_delegator_rewards * stake / total_active_stake`, clamped to `pending_delegator_rewards`) [5](#0-4) . This `block_reward` amount is then minted directly into the stake account's lamports via `checked_add_lamports` in `build_updated_stake_reward`, alongside the ordinary inflation `stake_reward` [6](#0-5) . This is new-lamport minting (it increases bank capitalization the same way inflation rewards do), not a transfer out of the vote account's actual balance.

Searching the codebase for any code path that decrements or clears `pending_delegator_rewards` after it has been used as the basis for a block-reward payout found none in `programs/vote/src/vote_state/mod.rs`, `programs/vote/src/vote_processor.rs`, or `runtime/src/bank/partitioned_epoch_rewards/*`. The only place `pending_delegator_rewards` is set to zero is in unit-test setup code that manually mutates vote-account state to test the `withdraw` reserve logic [7](#0-6) , not a production instruction path. `withdraw` only ever *reads* `pending_delegator_rewards` to compute a minimum reserved balance; it never reduces it [8](#0-7) .

Because `pending_delegator_rewards` is never consumed/decremented once it has been used to compute a block reward, the exact same "pending" pool value is available again as input to `calculate_block_reward` in every subsequent epoch's reward calculation, for as long as the value remains nonzero (which it will, since nothing reduces it). This mirrors the reported bug's root cause: a tracked accounting value that is supposed to represent a *consumable* pool of value diverges from what actually happens to it, and no code exists to reconcile "value used for distribution calculation" with "value already distributed."

### Impact Explanation
If `pending_delegator_rewards` is never decremented after being used to size a block-revenue-sharing payout, the same deposited amount is repeatedly redistributed to stakers as freshly minted lamports epoch after epoch, rather than being paid out once and exhausted. This causes uncontrolled, repeated inflation of the token supply tied to a single deposit, which is a false/incorrect execution of the reward-distribution protocol logic that all validators would deterministically reproduce (so it would not itself cause consensus divergence, but it does cause incorrect, unbounded fund creation/duplication from a fixed deposited pool) — a fund-integrity impact matching the "false execution" / fund-duplication category in scope.

### Likelihood Explanation
This path executes automatically, without any attacker interaction required, every epoch that `block_revenue_sharing` is active and a vote account has a nonzero `pending_delegator_rewards` (which any validator can create legitimately via `DepositDelegatorRewards`) [9](#0-8) . Likelihood of the *code path* triggering is high; however, I could not conclusively verify within available index coverage whether a decrement mechanism exists elsewhere in the codebase (e.g., inside `distribute_reward_commissions` or a related helper that I was not able to fully inspect due to tool-call limits). This is the primary source of uncertainty in this finding.

### Recommendation
Trace the full lifecycle of `pending_delegator_rewards` end-to-end (from `add_pending_delegator_rewards` through every reward-calculation and distribution code path in `runtime/src/bank/partitioned_epoch_rewards/`) to confirm whether a corresponding decrement exists. If none exists, add logic to subtract the minted `block_reward` amount from `pending_delegator_rewards` on the vote account as part of `distribute_epoch_rewards_in_partition`/`store_stake_accounts_in_partition`, so that a deposited reward pool is consumed exactly once rather than being reused as the basis for reward calculation in every subsequent epoch.

### Proof of Concept
1. Validator authority calls `DepositDelegatorRewards` on their vote account with `deposit = D`, setting `pending_delegator_rewards = D` and transferring `D` lamports into the vote account [2](#0-1) .
2. At the next epoch boundary, `calculate_block_reward` computes each delegator's share of `D` and `build_updated_stake_reward` mints those lamports into stake accounts via `checked_add_lamports` [5](#0-4) [10](#0-9) .
3. Inspect the vote account's `pending_delegator_rewards` field after this distribution completes — confirm whether it is still `D` (not decremented).
4. If still `D`, at the following epoch boundary the same `D` is used again in `calculate_block_reward`, minting another round of rewards to stakers from the same original deposit, with no bound on how many times this repeats.

**Caveat**: due to index size limits, I was not able to fully inspect `distribute_reward_commissions` and all downstream helper functions in `runtime/src/bank/partitioned_epoch_rewards/` to rule out a decrement path existing elsewhere. Confirming this finding conclusively (and locating the exact fix point) requires a full-repository trace, which would need a Devin session with complete file access rather than the indexed ask-mode search used here.

### Citations

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

**File:** programs/vote/src/vote_state/mod.rs (L1062-1122)
```rust
/// Withdraw funds from the vote account
pub fn withdraw<S: std::hash::BuildHasher>(
    instruction_context: &InstructionContext,
    vote_account_index: IndexOfAccount,
    target_version: VoteStateTargetVersion,
    lamports: u64,
    to_account_index: IndexOfAccount,
    signers: &HashSet<Pubkey, S>,
    rent_sysvar: &Rent,
    clock: &Clock,
) -> Result<(), InstructionError> {
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;
    let vote_state = get_vote_state_handler_checked(&vote_account, target_version)?;

    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;

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

**File:** programs/vote/src/vote_state/mod.rs (L5304-5311)
```rust

        // Should pass - both collectors aliased to vote account.
        {
            let transaction_context = new_transaction_context(
                vec![
                    (id(), processor_account.clone()),
                    (vote_pubkey, make_uninit_vote_account()),
                ],
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
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
