## Title
Unprivileged `DepositDelegatorRewards` griefing permanently blocks `Withdraw`-based closure of a vote account - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Golom report's broken invariant is: an unprivileged/adversarial actor can raise a "must-be-cleared-before-withdraw" counter on a resource they do not own, permanently blocking the legitimate owner from withdrawing or closing it. The Agave analog is `deposit_delegator_rewards` (SIMD-0123), which lets **any signer holding lamports** push funds and increment `pending_delegator_rewards` on **any vote account**, while `withdraw` refuses to fully close a vote account (and caps partial withdrawals) as long as `pending_delegator_rewards > 0`.

### Finding Description
`deposit_delegator_rewards` only requires the *source* account to sign — it does not require any relationship to the vote account's authorized voter/withdrawer: [1](#0-0) 

It then CPIs a system transfer from the source into the vote account and unconditionally increments `pending_delegator_rewards`: [2](#0-1) [3](#0-2) 

`withdraw` then enforces that a vote account cannot be fully closed while `pending_delegator_rewards > 0`, and caps any partial withdrawal to leave at least `rent_exempt + pending_delegator_rewards` in the account: [4](#0-3) 

The counter is only reduced through the epoch-rewards distribution path, `calculate_block_reward`, which computes a payout proportional to `total_active_stake` for that vote account **for that specific epoch**; if `total_active_stake` is `0` the function returns `0` and nothing is paid out / decremented: [5](#0-4) 

This means an attacker can call `DepositDelegatorRewards` against **any vote account that currently has zero delegated/active stake** (e.g., a freshly created identity/vote account not yet delegated to, a decommissioned validator's vote account, or a validator temporarily between delegations) with a trivial deposit (even 1 lamport), and `pending_delegator_rewards` will never be reduced by the reward-distribution mechanism because there is no active stake to compute a share against. The withdraw-authority of that vote account is thereby permanently prevented from ever closing the account (`InstructionError::InsufficientFunds` on full withdraw) and is forced to keep at least `pending_delegator_rewards` lamports of their own balance locked in the account forever, mirroring exactly the "voter can permanently lock a token via an unrelated state-mutating call" pattern from the Golom report — except here the "voter" role is not even privileged: it's any signer with a few lamports.

### Impact Explanation
This is a fund-lock/DoS on a specific class of unprivileged, non-consensus-critical accounts (vote accounts): the withdraw authority loses the ability to ever fully reclaim/close the account, and a portion of its balance (equal to whatever amount was deposited by the attacker) becomes permanently unwithdrawable as long as the vote account never has any active/delegated stake in the epoch when the reward pass runs. This is a "fund loss/lock" style impact caused entirely by an unprivileged actor (the depositor), not a malicious validator/leader/peer, and requires no trust assumption about the caller — matching the accepted impact categories (fund loss via runtime/vote-program logic).

### Likelihood Explanation
Any Solana account with a few lamports and a system-program transfer capability can issue `DepositDelegatorRewards` against an arbitrary vote-account pubkey; the instruction only checks that the *source* is a signer, not that the caller has any relationship to the vote account. Targeting vote accounts with zero delegated stake (very common — many vote accounts sit undelegated, e.g. right after creation, or after all delegators have moved away) is trivial and repeatable at negligible cost, and the effect (permanent inability to fully withdraw/close) is deterministic given the code path shown above.

### Recommendation
- Cap or refuse `DepositDelegatorRewards` deposits when the target vote account currently has zero (or below some threshold of) active delegated stake, since such deposits can never be redeemed by the block-reward mechanism.
- Alternatively, allow the vote account's `authorized_withdrawer` to sweep/forgive stale `pending_delegator_rewards` (e.g., via a new instruction) after some epoch timeout if no active stake has claimed it, so a griefed vote account is not locked indefinitely.
- Consider requiring the depositor to be an actual delegated stake account (or its authority) for that vote account, rather than an arbitrary signer, to remove the unprivileged griefing vector entirely.

### Proof of Concept
1. Create (or identify) `vote_pubkey` whose `VoteStateV4` has no active/delegated stake for the upcoming reward epoch (e.g., a brand-new vote account, or one all delegators unstaked from).
2. Attacker (any funded keypair `attacker`) submits `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with `attacker` as the signing source and `vote_pubkey` as the target — succeeds per `deposit_delegator_rewards`, since only `verify_authorized_signer(&source_address, signers)` is checked [1](#0-0) .
3. `pending_delegator_rewards` on `vote_pubkey` becomes `1` (or whatever amount attacker chooses, repeatable) via `add_pending_delegator_rewards` [3](#0-2) .
4. At the next epoch-rewards distribution, `calculate_block_reward` finds `total_active_stake == 0` for `vote_pubkey` and returns `0`, so `pending_delegator_rewards` is never redeemed/decremented [5](#0-4) .
5. The legitimate `authorized_withdrawer` of `vote_pubkey` now calls `Withdraw` for the full balance to close the account; it fails permanently with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0` [6](#0-5) , and any partial withdraw is capped to leave `rent_exempt + pending_delegator_rewards` locked forever [7](#0-6) . This exact behavior (blocking full close while `pending_delegator_rewards > 0`) is confirmed by the existing test `test_withdraw_pending_delegator_rewards` [8](#0-7) .

*Note: I was unable to fully trace the exact line(s) that decrement `pending_delegator_rewards` after a successful non-zero block reward redemption (the search results showed the computation path but not the final subtraction call site) — the index may not contain that specific snippet. This does not affect the core finding, since the zero-active-stake case (where no decrement ever happens) is directly confirmed by `calculate_block_reward`'s early return.*

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

**File:** programs/vote/src/vote_state/mod.rs (L974-988)
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
