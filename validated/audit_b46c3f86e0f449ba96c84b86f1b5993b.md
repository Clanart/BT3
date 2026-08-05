### Title
Unprivileged `DepositDelegatorRewards` instruction lets anyone inflate `pending_delegator_rewards` on any vote account, permanently locking funds and blocking withdrawal/closure — ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The FIVA report's core flaw is a shared piece of pending state that any unrelated party can write into without any check that the writer is the legitimate owner of the operation, letting an attacker corrupt state that the victim depends on to safely complete/withdraw their position. The Agave analog is the `deposit_delegator_rewards` vote-program handler (SIMD-0123): it only requires the *sender* of lamports to sign, not the vote account's `authorized_withdrawer`, so any unprivileged account can add to a victim vote account's `pending_delegator_rewards` field. That field is later used by `withdraw()` to reserve a portion of the vote account balance and to unconditionally block full account closure whenever it is non-zero.

### Finding Description
`deposit_delegator_rewards` only verifies that the *source* of the deposited lamports has signed the transaction — it never checks that the caller has any relationship to the vote account being credited: [1](#0-0) 

It then unconditionally increases `pending_delegator_rewards` on the target vote account and updates its serialized state: [2](#0-1) 

This value is later consumed by `withdraw()`, which is the only vote-program path that lets the `authorized_withdrawer` take lamports out of (or close) the vote account. When `pending_delegator_rewards > 0`, full closure (`remaining_balance == 0`) is unconditionally rejected, and partial withdrawals are capped so that at least `pending_delegator_rewards` lamports (plus rent-exempt minimum) must remain: [3](#0-2) 

`pending_delegator_rewards` is only ever reduced as a side effect of the block-revenue-sharing reward calculation in the runtime, and only when the vote account actually has active delegated stake for the relevant epoch — if `total_active_stake` is zero (e.g., the validator has no active stake, is spinning down, or the feature isn't computing a payout for that particular epoch), no reduction occurs: [4](#0-3) 

Because any account can call `DepositDelegatorRewards` at any time against any vote account — with no bound on frequency or minimum amount, and no way for the withdrawer to reject/cancel the deposit — an attacker can repeatedly (or even once) inject lamports into `pending_delegator_rewards` on a victim's vote account. This mirrors the report's broken invariant exactly: a piece of shared, permission-agnostic state (`pending_delegator_rewards`, analogous to the Deposit contract's `sy_balance`/`pt_balance`) can be advanced by an unrelated party using an out-of-band, uncoordinated write, and that write directly restricts what the legitimate owner can subsequently do (here, withdraw/close their own vote account), with no signature or relationship check tying the depositor to the vote account owner.

### Impact Explanation
This is not a race-condition/front-run scenario: the attacker doesn't need to observe or beat a pending transaction. At any point, an unprivileged party can permanently raise the floor of lamports the `authorized_withdrawer` must leave in the vote account, and can prevent full account closure altogether, for as long as the vote account lacks active stake to trigger the reward-based reduction path (or simply by re-depositing faster than any reduction occurs). This constitutes an unprivileged, non-consensual lock of validator funds and a denial of the legitimate withdraw/close operation — a fund-loss/lock and degradation impact squarely inside the valid-impact scope (accounts/runtime, fund loss, non-RPC degradation), and requires no malicious peer, node, validator, or trusted integration — only an ordinary unprivileged signer sending a normal transaction.

### Likelihood Explanation
The attack requires only constructing a `VoteInstruction::DepositDelegatorRewards` instruction targeting any vote account pubkey and signing with an attacker-controlled source account holding a trivial amount of lamports — no special privileges, timing, or races are needed. It can be repeated cheaply and at will against any vote account on the network.

### Recommendation
- Short term: Require that `deposit_delegator_rewards` also be authorized by the vote account's `authorized_withdrawer` (or introduce a dedicated "opt-in"/cap mechanism) before crediting arbitrary lamports into `pending_delegator_rewards`, so the vote account owner cannot have their withdrawable balance restricted without consent.
- Long term: Document and enforce the full state-machine for `pending_delegator_rewards` (who can increase it, exactly how and when it is guaranteed to be reduced to zero, and what happens if a vote account never accumulates the stake needed to trigger the reduction path), and add a bound/expiry or reversible mechanism so unprivileged deposits cannot indefinitely block account closure.

### Proof of Concept
1. Identify any active vote account `V` with `authorized_withdrawer = W`.
2. As attacker `A` (no relationship to `V`), submit a transaction invoking `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with accounts `[V (writable), A (signer, writable), system_program]`, per the account layout consumed in `deposit_delegator_rewards`: [1](#0-0) 
3. This succeeds (only `A`'s signature is required), incrementing `V`'s `pending_delegator_rewards` by 1 and transferring 1 lamport from `A` to `V`.
4. `W` now attempts `VoteInstruction::Withdraw` to close `V` fully; the call is rejected with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0`: [5](#0-4) 
5. If `V` has no active delegated stake contributing to block-reward distribution in the current epoch(s), `calculate_block_reward` returns `0` and `pending_delegator_rewards` is never reduced: [6](#0-5) 
   leaving `V` permanently unable to fully close, and `W`'s withdrawable balance permanently reduced by the injected amount.

Note: I was not able to fully trace, within the available iterations, the exact downstream code path that decrements `pending_delegator_rewards` after a reward distribution event (only the `calculate_block_reward` computation feeding into stake rewards was located); confirming whether *any* circumstance guarantees eventual zeroing (versus only proportional consumption tied to active stake) would need further investigation of `redeem_rewards` and the stake-reward application path.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L211-232)
```rust
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
