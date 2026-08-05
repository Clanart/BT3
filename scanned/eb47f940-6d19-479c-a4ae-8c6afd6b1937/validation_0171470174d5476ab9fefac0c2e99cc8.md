### Title
Vote Account `Withdraw` Allows Deinitializing Vote State via Self-Transfer Without Loss of Lamports - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The vote program's `withdraw` function subtracts lamports from the vote account and credits them to a separate "destination" account instruction index, but never verifies that the destination account is different from the vote account itself. [1](#0-0)  Because the "should we deinitialize/close the account" decision is made using an *intermediate* projected balance rather than the actual final balance, an authorized withdrawer can pass the vote account itself as both the source and destination. This wipes/deinitializes the vote account's state (epoch credits, authorized voter/withdrawer history, pending rewards bookkeeping) exactly like a full closure, while the final lamport balance is restored to its original value because the debit and the credit net to zero on the same account.

### Finding Description
`withdraw()` computes `remaining_balance = vote_account.get_lamports() - lamports` and, if that value is `0`, deinitializes the vote account's state via `VoteStateHandler::deinitialize_vote_account_state` (subject to `pending_delegator_rewards == 0` and the "active vote account" cool-down check). [2](#0-1)  Only *after* this decision does the code perform the actual lamport movement:

```
vote_account.checked_sub_lamports(lamports)?;
drop(vote_account);
let mut to_account = instruction_context.try_borrow_instruction_account(to_account_index)?;
to_account.checked_add_lamports(lamports)?;
``` [3](#0-2) 

If `vote_account_index` and `to_account_index` refer to the same underlying account (the withdrawer supplies the vote account pubkey as its own "destination"), then `checked_sub_lamports(lamports)` followed by `checked_add_lamports(lamports)` nets to the original balance — the account's lamports are unaffected. However, the deinitialization branch above has already executed based on the *would-be* zero balance, permanently discarding the vote account's data (vote history, credits, delegated authorities) as if the account had actually been drained to zero and closed.

This is the same broken invariant described in the source report: an operation that computes and commits an intermediate "post-withdraw" state without recognizing that source and destination are aliased, so only the final (here, unchanged) lamport value is observed while the side effect tied to the intermediate value (deinitialization) is incorrectly applied.

Notably, the vote program *does* have precedent for guarding against this exact class of bug elsewhere: the newer `deposit_delegator_rewards` instruction explicitly rejects the case where the source account equals the destination account, returning `InstructionError::InvalidArgument`, as shown by its test coverage. [4](#0-3)  No equivalent `from_account_index != to_account_index` check exists in `withdraw()`, indicating the same defensive pattern was not applied consistently across all balance-moving vote-program operations.

### Impact Explanation
This causes false state acceptance in the runtime: a vote account can be forced through the "close" code path — resetting all its bookkeeping (epoch credits used in leader/reward calculations, authorized voter/withdrawer records, commission, root slot, etc.) — while retaining its full lamport balance and rent-exempt status. This corrupts consensus-relevant accounting state (vote history integrity) without an accompanying, expected loss of funds, and can be used to erase delinquency/performance history or bypass the `ActiveVoteAccountClose` protection intended to stop active validators from clearing their own state at will.

### Likelihood Explanation
The withdrawer authority can trigger this deterministically and unprivilegedly by simply constructing a `Withdraw` instruction where the destination account passed matches the vote account's own key and the withdrawal amount equals the vote account's current lamport balance. No other party or trust assumption is required.

### Recommendation
Add an explicit check in `withdraw()` mirroring the one already used in `deposit_delegator_rewards`: reject the instruction with `InstructionError::InvalidArgument` (or equivalent) if `vote_account_index == to_account_index` (or, more precisely, if the resolved account keys are identical), before evaluating the `remaining_balance == 0` deinitialization branch.

### Proof of Concept
1. Create/own a vote account with authorized withdrawer signing rights and no pending delegator rewards, past the epoch-credit cool-down so `reject_active_vote_account_close` is `false`.
2. Submit a `Withdraw` instruction where `lamports` equals the vote account's entire current balance, and pass the vote account's own pubkey as the destination ("to") account in the instruction accounts list.
3. `withdraw()` computes `remaining_balance == 0`, deinitializes the vote account state via `VoteStateHandler::deinitialize_vote_account_state`. [5](#0-4) 
4. `checked_sub_lamports(lamports)` then `checked_add_lamports(lamports)` on the same account nets to the original balance — the account still holds its full lamports post-transaction. [3](#0-2) 
5. Result: the vote account retains its lamports but its on-chain vote state (history/credits/authorities) has been wiped, despite no legitimate full withdrawal having occurred.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L1062-1074)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1079-1111)
```rust
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1124-1128)
```rust
    vote_account.checked_sub_lamports(lamports)?;
    drop(vote_account);
    let mut to_account = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to_account.checked_add_lamports(lamports)?;
    Ok(())
```

**File:** programs/vote/src/vote_processor.rs (L5026-5054)
```rust
        // Fail - source account == destination account.
        process_instruction_with_cu_check(
            VoteProgramFeatures::all_enabled(),
            &instruction_data,
            vec![
                (vote_pubkey, vote_account_v4.clone()),
                (
                    solana_sdk_ids::system_program::id(),
                    AccountSharedData::new(0, 0, &solana_sdk_ids::native_loader::id()),
                ),
            ],
            vec![
                AccountMeta {
                    pubkey: vote_pubkey,
                    is_signer: false,
                    is_writable: true,
                },
                AccountMeta {
                    pubkey: vote_pubkey, // Duplicated
                    is_signer: true,
                    is_writable: true,
                },
                AccountMeta {
                    pubkey: solana_sdk_ids::system_program::id(),
                    is_signer: false,
                    is_writable: false,
                },
            ],
            Err(InstructionError::InvalidArgument),
```
