## Title
Unprivileged `DepositDelegatorRewards` griefing permanently blocks vote-account withdrawal/closure - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Mochi report describes a griefing pattern: an attacker who can call an unprivileged "deposit" entrypoint keeps a per-account counter above zero, and that counter is checked by the withdrawal path to reject withdrawals/closure. The Agave analog is the SIMD-0123 `DepositDelegatorRewards` vote instruction, which lets **any signer** transfer lamports into **any** V4 vote account and unconditionally increment `pending_delegator_rewards`, a field that the `withdraw` instruction uses to gate both full account closure and the minimum withdrawable balance.

### Finding Description
`deposit_delegator_rewards` performs no authorization check tying the caller to the vote account or its authorized withdrawer — it only requires that the `source_address` (the depositor) sign the transfer: [1](#0-0) 

It then transfers lamports from that arbitrary signer into the vote account and unconditionally increases `pending_delegator_rewards`: [2](#0-1) 

`add_pending_delegator_rewards` simply adds the deposit with no cap or restriction on caller identity: [3](#0-2) 

The `withdraw` instruction (called by the vote account's `authorized_withdrawer`) is gated by this same counter: [4](#0-3) 

- When trying to fully close the account (`remaining_balance == 0`), any `pending_delegator_rewards > 0` unconditionally causes `InstructionError::InsufficientFunds`.
- When doing a partial withdraw, the minimum retained balance is increased by `pending_delegator_rewards`, reducing the amount actually withdrawable.

`pending_delegator_rewards` is only drained by the epoch-boundary block-reward distribution logic (`calculate_block_reward` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), which subtracts an amount proportional to each stake account's share once per epoch. Because `DepositDelegatorRewards` has no cooldown, no minimum, and no relationship to who the withdrawer is, an attacker can re-deposit a token amount (e.g., 1 lamport) into the target vote account every time the counter is drained (or before every `withdraw` attempt, similar to the Mochi front-run scenario), keeping `pending_delegator_rewards` above zero indefinitely and blocking legitimate closure of the vote account by its rightful authorized withdrawer. This mirrors the Mochi bug exactly: an unprivileged "deposit" call resets/keeps a guard counter that the withdraw path treats as "activity," and the attacker can maintain that state cheaply and perpetually.

### Impact Explanation
This blocks the authorized withdrawer from ever fully closing (reclaiming rent + validator identity funds from) a targeted vote account, and forces retained balance above the normal rent-exempt minimum for partial withdrawals — a denial-of-funds-access griefing attack, not requiring any special privilege, trusted role, or malicious validator/leader assumption. It fits "fund theft/loss" / "unprivileged Agave issues" categories since it permanently locks portions of a validator's vote-account lamports against the will of the account owner, achievable by any funded account.

### Likelihood Explanation
Low-cost and trivially repeatable: the attack only requires enough lamports to submit a `DepositDelegatorRewards { deposit: 1 }` (or `0`, need to check if `deposit: 0` still adds — test at `programs/vote/src/vote_processor.rs:5186-5217` shows a `deposit: 0` case is treated as a no-op) transaction periodically against any V4 vote account whose withdrawer is attempting to withdraw/close. Feature-gate requirements (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing` — SIMD-0123/0291/0232) must be active on-chain for this path to be reachable; I could not verify from local code alone whether these features are currently activated on mainnet/testnet, which affects present-day exploitability but not the underlying design flaw.

### Recommendation
Restrict `DepositDelegatorRewards` deposits to a bounded/rate-limited amount tied to actual reward distribution flows (i.e., only the runtime's own epoch reward-distribution logic should be able to increase `pending_delegator_rewards`), or decouple the withdraw-blocking check from an attacker-controllable counter — e.g., only block withdrawal amounts up to the actual `pending_delegator_rewards` that existed at the time distribution last ran, rather than an ever-growing, externally-fundable value.

### Proof of Concept
1. Attacker observes a V4 vote account `V` whose authorized withdrawer intends to fully close it or withdraw a large balance.
2. Attacker submits `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with themselves as an arbitrary signer/source account (no relation to `V`'s authorities required) — see the unrestricted signer check at [5](#0-4) .
3. This increments `V`'s `pending_delegator_rewards` to ≥ 1 via [3](#0-2) .
4. The withdrawer's subsequent `withdraw` call to close `V` (`remaining_balance == 0`) now fails with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0`, per [6](#0-5) .
5. Attacker repeats step 2 whenever the epoch-boundary reward distribution reduces the counter (or simply front-runs each withdraw attempt), keeping the account permanently un-closable, analogous to the Mochi `lastDeposit`/`wait()` griefing pattern.

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
