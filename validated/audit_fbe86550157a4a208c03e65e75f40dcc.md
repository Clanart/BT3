## Title
Unprivileged `DepositDelegatorRewards` lets anyone permanently block full withdrawal/closure of a target vote account - (File: `programs/vote/src/vote_state/mod.rs`)

## Summary
The DYAD bug allowed anyone to block a privileged "remove" action on a target's position by depositing a small amount of an asset into it before the removal transaction executed, since removal requires the position to hold zero assets. Agave's vote program has an analogous invariant: `withdraw()` only allows a vote account to be fully closed (`remaining_balance == 0`) when `pending_delegator_rewards == 0`. The `pending_delegator_rewards` field, however, can be incremented by *any* signer via `VoteInstruction::DepositDelegatorRewards`, which requires only that the depositing "source" account sign — not the vote account's authorized withdrawer.

## Finding Description
`vote_state::withdraw` enforces that a full close is rejected if `pending_delegator_rewards > 0`: [1](#0-0) 

and even a partial withdrawal must leave behind `pending_delegator_rewards` as part of the required minimum balance: [2](#0-1) 

`deposit_delegator_rewards` is the function that increments this field. It only verifies that the *sender* (the account transferring lamports in) has signed — it does not check that the caller is the vote account's authorized withdrawer or otherwise privileged with respect to that vote account: [3](#0-2) 

The CPI transfer moves lamports from the (attacker-controlled) source into the target vote account, then unconditionally increases `pending_delegator_rewards`: [4](#0-3) 

The processor wiring confirms the only accounts required are the vote account and the sender/source account, with no authorized-withdrawer check gating this instruction: [5](#0-4) 

This is a direct structural analog to the DYAD finding: any unprivileged party can deposit into a resource they don't control (a dNFT position / a vote account) in a way that trips a guard (`VaultHasAssets` / `pending_delegator_rewards > 0`) blocking the resource owner's privileged action (`remove()` / full `withdraw()`). Unlike the DYAD case, this Agave path does **not** require mempool front-running of the specific withdrawal transaction — the attacker can deposit 1 lamport worth of "delegator rewards" into any target vote account at any time, well in advance, to pre-emptively and repeatedly block that account from ever being fully closed, since there is no privileged/self-directed way found in this code path for the withdrawer to zero out `pending_delegator_rewards` without going through this same open-to-anyone deposit mechanic in reverse.

## Impact Explanation
An attacker can, for negligible cost, permanently prevent a validator/vote-account owner from fully closing (self-destructing) their vote account via `Withdraw`, and can force the withdrawer to always leave `pending_delegator_rewards` lamports locked in the account as part of the enforced minimum balance. This is a fund-availability/lock impact on an unprivileged party's own funds, triggered purely by an attacker with no special access, satisfying the "fund theft/loss" class via permanent inability to reclaim the withdrawer's own lamports tied up as `pending_delegator_rewards`.

## Likelihood Explanation
High feasibility: the instruction requires no special permissions on the target vote account — any keypair can sign as the "source" and send an arbitrary small deposit (subject to feature-gate activation of `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing`). No timing/front-running is needed since the deposit can be made proactively and repeated indefinitely.

## Recommendation
Restrict who may increase `pending_delegator_rewards` on a vote account not owned by them, or provide the authorized withdrawer with a way to unilaterally clear/consume `pending_delegator_rewards` (analogous to the DYAD fix of letting the owner override the "has assets" block with an explicit acknowledgment), rather than making full closure/withdrawal permanently contingent on a value any outsider can inflate at will.

## Proof of Concept
1. Alice creates and initializes a V4 vote account and later wants to fully withdraw/close it via `Withdraw(lamports)` where `lamports == account.lamports()`.
2. Bob, an unrelated party, calls `DepositDelegatorRewards { deposit: 1 }` with himself as the signing "source" account and Alice's vote account as the target — no authorization from Alice or her withdrawer key is required, per [3](#0-2)  and the accounts wiring in [5](#0-4) .
3. This CPIs a lamport transfer into Alice's vote account and sets `pending_delegator_rewards = 1` per [4](#0-3) .
4. Alice's subsequent full-withdraw call now fails: the check at [1](#0-0)  rejects the close because `pending_delegator_rewards > 0`, and any partial withdraw permanently must exclude that amount per [2](#0-1) .
5. Bob can repeat step 2 at any time (even proactively, before Alice attempts to withdraw), so this griefing is not dependent on winning a mempool race against a specific transaction.

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

**File:** programs/vote/src/vote_state/mod.rs (L1112-1122)
```rust
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
