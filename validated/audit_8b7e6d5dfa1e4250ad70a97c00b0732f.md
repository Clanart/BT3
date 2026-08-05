### Title
Unprivileged `DepositDelegatorRewards` griefing permanently blocks vote-account closure - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The 1inch `FixedRateSwap` report describes a shared-state invariant (token-1 reserve) that any unprivileged party can drive to a degenerate value, permanently blocking a legitimate operation (the swap) for everyone else. The Agave analog is the vote program's `pending_delegator_rewards` field (SIMD-0123): any unprivileged signer can call `DepositDelegatorRewards` to push this counter above zero on *any* vote account, and the `withdraw` instruction unconditionally refuses to fully close/deinitialize a vote account while `pending_delegator_rewards > 0`.

### Finding Description
`deposit_delegator_rewards` only requires that the *source* account (the depositor) sign the transfer — it performs no check that the caller has any relationship to the vote account or its authorized withdrawer: [1](#0-0) 

It then unconditionally increments the vote account's `pending_delegator_rewards` by the deposited amount: [2](#0-1) 

`add_pending_delegator_rewards` simply does a `checked_add` with no upper bound or authorization gate: [3](#0-2) 

The `withdraw` instruction, used by the authorized withdrawer to close a vote account, hard-fails whenever `pending_delegator_rewards > 0` and the withdraw would zero the account balance: [4](#0-3) 

`pending_delegator_rewards` is only reduced during epoch reward distribution, proportionally to the vote account's *active stake* (`calculate_block_reward`): [5](#0-4) 

If `total_active_stake` for the vote account is `0`, `calculate_block_reward` returns `0` unconditionally: [6](#0-5) 

This means any vote account with no (or insufficient) delegated stake at reward-distribution time never has its `pending_delegator_rewards` reduced. An attacker needs only 1 lamport and a signature to call `DepositDelegatorRewards` against a target vote account (e.g. a newly created, not-yet-delegated, or fully-undelegated vote account) to set `pending_delegator_rewards = 1`, which then can never be driven back to `0` by the reward mechanism, permanently blocking the authorized withdrawer from ever fully closing/deinitializing that vote account via `withdraw`.

### Impact Explanation
This does not cause direct fund theft (the deposited lamports go to the target, not the attacker), but it is a functional denial-of-service on a specific vote-account operation: the authorized withdrawer can never zero-out and deinitialize the vote account (only partial withdrawals down to `rent_exempt_minimum + pending_delegator_rewards` remain possible). This mirrors the reported bug class exactly — an unprivileged actor manipulating a small, shared/global balance-like field to permanently block a legitimate state transition for another party — and matches the "false execution/acceptance" / unprivileged blocking category for built-in programs.

### Likelihood Explanation
The `DepositDelegatorRewards` instruction is fully permissionless for any account willing to sign and transfer lamports (no relation to the vote account's identity, authorized voter, or withdrawer is required), and the deposit amount can be as small as 1 lamport, making the attack trivially cheap and repeatable against any vote account that currently has zero or negligible active stake (e.g., freshly created accounts prior to delegation, or accounts whose stake has fully cooled down).

### Recommendation
- Restrict `DepositDelegatorRewards` to only be invocable in contexts where it is tied to actual delegator/stake relationships (e.g., only callable via the stake program's reward-crediting path) rather than as an open, permissionless instruction any signer can call directly on an arbitrary vote account.
- Alternatively, allow the authorized withdrawer to forcibly clear/forfeit `pending_delegator_rewards` (e.g., by sweeping it to the destination on close) instead of hard-blocking closure indefinitely when no stake exists to ever repay it.
- Add a floor/guard so that when `total_active_stake == 0` for a vote account, any outstanding `pending_delegator_rewards` can still be resolved (paid out or written off) so it does not become a permanently stuck value.

### Proof of Concept
1. Attacker creates or targets a vote account `V` that currently has no delegated/active stake (e.g., right after `InitializeAccount`, before any stake delegates to it, or after all delegated stake has fully deactivated).
2. Attacker submits a `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` instruction with themselves as the signing/source account and `V` as the vote account — no relation to `V`'s authorized withdrawer or voter is required, per `deposit_delegator_rewards`'s only check being `verify_authorized_signer(&source_address, signers)` on the *source*, not the vote account.
3. This CPIs 1 lamport into `V` and sets `vote_state.pending_delegator_rewards = 1` via `add_pending_delegator_rewards`.
4. Because `V` has `total_active_stake == 0` in `calculate_block_reward`, epoch-reward distribution never reduces `pending_delegator_rewards` back to `0`.
5. `V`'s authorized withdrawer subsequently calls `Withdraw(all_lamports)` to close the account; `withdraw` hits `if pending_delegator_rewards > 0 { return Err(InstructionError::InsufficientFunds) }` and permanently fails, per the guard at `programs/vote/src/vote_state/mod.rs:1087-1092`, confirmed by the existing test `test_withdraw_pending_delegator_rewards` in `programs/vote/src/vote_processor.rs:5219-5314` showing full-close is rejected whenever `pending_delegator_rewards > 0`.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L936-951)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L980-988)
```rust
    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
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
