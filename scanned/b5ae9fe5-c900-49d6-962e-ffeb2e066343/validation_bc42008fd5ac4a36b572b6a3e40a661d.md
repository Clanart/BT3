### Title
Unprivileged `DepositDelegatorRewards` lets anyone permanently lock a vote account's lamports by setting `pending_delegator_rewards` on a zero-stake account - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
Just like the MuteAmplifier bug — where a permissionless "first stake" branch could be triggered after `endTime` to leave the contract with a non-zero staker and no way to ever drain the locked rewards via `rescueTokens` — the Agave vote program's `deposit_delegator_rewards` instruction lets *any* signer irreversibly bump a vote account's `pending_delegator_rewards` field. If that vote account currently has zero (or later drops to zero) active delegated stake, the reward-draining path (`calculate_block_reward`) can never reduce `pending_delegator_rewards` back to zero, and `withdraw` then permanently refuses to fully close the account or withdraw below the rent-exempt-plus-pending-rewards floor. This is a permanent, front-runnable fund lock caused by an unguarded initializer-style code path, the same bug class as the source report.

### Finding Description
`deposit_delegator_rewards` requires only that the *source* account sign the transfer; it performs no check on the target vote account's authority, stake, or state beyond deserializing it as V4: [1](#0-0) [2](#0-1) 

Anyone can therefore call this instruction against an arbitrary vote account and set `pending_delegator_rewards` to a non-zero value via `add_pending_delegator_rewards`.

`withdraw` treats a non-zero `pending_delegator_rewards` as a hard blocker: closing the account (draining it to zero lamports) is rejected outright, and any partial withdrawal is bounded below by `rent_exempt_minimum + pending_delegator_rewards`: [3](#0-2) 

`pending_delegator_rewards` is only ever decremented through the block-reward distribution path, which is strictly proportional to the vote account's active delegated stake for the rewarded epoch: [4](#0-3) 

If `total_active_stake` for that vote account is `0`, `calculate_block_reward` returns `0` unconditionally — there is no other mechanism anywhere in the codebase that reduces `pending_delegator_rewards`. The corrupted value is thus `pending_delegator_rewards` on a vote account with zero active stake: once set above zero, it is mathematically unreachable to bring back to zero, exactly mirroring how MuteAmplifier's `firstStakeTime`/`totalStakers` combo became permanently non-zero-but-unpayable once a single stake landed after `endTime`.

### Impact Explanation
This is a direct, unprivileged fund-lock: the authorized withdrawer of a vote account can be griefed by any third party depositing even 1 lamport of "delegator rewards" into a vote account that has no active stake (e.g., a freshly created vote account, or one whose validator has fully deactivated/unstaked). After that:
- The vote account can never be fully closed via `withdraw` (`remaining_balance == 0` branch always errors while `pending_delegator_rewards > 0`).
- The withdrawer can never withdraw more than `balance - rent_exempt_minimum - pending_delegator_rewards`, permanently locking at least the deposited amount (plus the rent-exempt buffer) inside the account.

This is a loss of access to funds for the legitimate account owner triggered purely by an unprivileged instruction call — squarely in the "fund theft/loss" impact category via a broken invariant in the accounts/runtime path, not a malicious-validator or trusted-plugin scenario.

### Likelihood Explanation
No special privileges, stake, or validator role are required — only the ability to sign a transfer of an arbitrary (even minimal) lamport amount and submit a `DepositDelegatorRewards` instruction. The precondition (vote account with zero/negligible active delegated stake) is common for new vote accounts before they attract delegators, or for vote accounts a validator is winding down. There is no cooldown, minimum-stake requirement, or authority check gating this call, so the attack is trivially and cheaply repeatable against any target.

### Recommendation
Add either of:
1. An authority/permission check on `deposit_delegator_rewards`, restricting who may increase `pending_delegator_rewards` on a given vote account (e.g., require the withdraw authority to opt in, or restrict the caller to the block-reward-distribution CPI path only), or
2. A guard preventing `deposit_delegator_rewards` from being called at all when the target vote account has zero active delegated stake for the current/next epoch, mirroring the missing `endTime`-style check that should have applied uniformly rather than being skipped on the "first" write.
Additionally, `withdraw` should provide a recovery path (e.g., an admin/authority override or a burn-to-inactive route) for `pending_delegator_rewards` that can never be drained because the vote account has no stakers to distribute to.

### Proof of Concept
1. Create vote account `V` with authorized withdrawer `W`; do not delegate any stake to `V` (or wait until all delegated stake to `V` is deactivated).
2. Any attacker `A` (unrelated signer) submits a transaction calling `deposit_delegator_rewards(V, A, 1, signers={A})`. This succeeds because the function only checks that the source (`A`) signs the CPI transfer: [1](#0-0) 
This sets `pending_delegator_rewards = 1` on `V`.
3. Since `V` has `total_active_stake == 0`, epoch-boundary reward distribution never reduces `pending_delegator_rewards` (`calculate_block_reward` returns `0`): [4](#0-3) 
4. `W` now tries to withdraw all lamports from `V` to close it. `withdraw` computes `pending_delegator_rewards = 1 > 0` and returns `InsufficientFunds`, permanently preventing account closure: [5](#0-4) 
`W` also cannot withdraw below `rent_exempt_minimum + 1` lamport ever again: [6](#0-5) 

Note: I was unable to fully trace whether `deposit_delegator_rewards` is currently gated behind an unactivated feature flag in this snapshot (SIMD-0123 references suggest it is feature-gated); if it is not yet active on mainnet, this issue would apply once the feature activates. This should be confirmed against the feature-set activation status before treating it as immediately exploitable.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-213)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
```
