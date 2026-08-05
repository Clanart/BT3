Audit Report

## Title
VAT eligibility filter and burn logic ignore `pending_delegator_rewards`, allowing the Validator Admission Ticket (VAT) burn to consume delegator-owed reward reserves - ([File: vote/src/vote_account.rs])

## Summary
`VoteAccounts::clone_and_filter_for_vat` computes `has_balance` from raw `vote_account.lamports()` without subtracting `pending_delegator_rewards`, a SIMD-0123 liability that is already credited into the account's lamport balance but is contractually reserved for delegators. `Bank::maybe_burn_vat_from_staked_accounts` then unconditionally subtracts `vat_to_burn_per_epoch` from that same raw lamport balance, so a vote account that passes the naive eligibility check can have its delegator-reserved rewards eroded by the VAT burn — a reserve that the vote program's own `withdraw` instruction otherwise treats as untouchable.

## Finding Description
The vote program's `withdraw` instruction enforces that any non-zero remaining balance must be at least `rent_exempt_minimum + pending_delegator_rewards`, explicitly protecting delegator-owed lamports from being withdrawn by the vote account authority itself: [1](#0-0) 

However, `VoteAccounts::clone_and_filter_for_vat`'s eligibility filter only checks raw lamports against `minimum_vote_account_balance`, with no adjustment for `pending_delegator_rewards`: [2](#0-1) 

Once an account passes this filter and is included among the top `MAX_ALPENGLOW_VOTE_ACCOUNTS`, `Bank::maybe_burn_vat_from_staked_accounts` subtracts `vat_to_burn_per_epoch` directly from `account.lamports()` via `checked_sub`, which only guards against arithmetic underflow of the raw balance — not against violating the `pending_delegator_rewards` reserve: [3](#0-2) 

The comment at this call site asserts that "Vote accounts have already been filtered ... to contain enough balance," but that filtering (`has_balance`) never accounted for `pending_delegator_rewards`, so the invariant it relies on is not actually established anywhere in the codebase.

## Impact Explanation
This is a fund-accounting-correctness defect in the runtime's reward/VAT bookkeeping (target scope: runtime/accounts fund loss). The exact corrupted value is the vote account's lamport balance relative to its `pending_delegator_rewards` reserve: the VAT burn can drive `lamports` below `rent_exempt_reserve + pending_delegator_rewards`, silently consuming lamports that are already recorded elsewhere in the same account state (`pending_delegator_rewards`) as owed to delegators. This affects delegator payouts once those rewards are eventually withdrawn, since the vote program's own `withdraw` invariant can no longer be satisfied for the full recorded amount.

## Likelihood Explanation
No malicious actor or privileged control is required. This occurs deterministically for any vote account that (a) has accrued `pending_delegator_rewards > 0` via the normal SIMD-0123 reward-distribution path, and (b) has a lamport balance that clears the naive `has_balance`/`minimum_vote_account_balance` check but not `rent_exempt_reserve + pending_delegator_rewards + vat_to_burn_per_epoch`. Because the state transition is applied identically by every validator during epoch-boundary processing, it is not a theoretical edge case but a systematic consequence of the missing reserve check.

## Recommendation
Update `clone_and_filter_for_vat`'s `has_balance` computation, and `maybe_burn_vat_from_staked_accounts`'s subtraction, to require `lamports >= rent_exempt_reserve + pending_delegator_rewards + minimum_vote_account_balance_for_vat` (respectively `+ vat_to_burn_per_epoch` for the burn), mirroring the reserve enforcement already present in `withdraw`.

## Proof of Concept
1. Drive a vote account's `pending_delegator_rewards` to `R > 0` via the SIMD-0123 reward-crediting path (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), which increases the account's lamport balance by `R` while recording the same amount in `pending_delegator_rewards`.
2. Set total lamports to `rent_exempt_reserve + R + vat_to_burn_per_epoch`, which passes `has_balance` in `vote/src/vote_account.rs:226` since that check ignores `R`.
3. Trigger the epoch-boundary call to `Bank::maybe_burn_vat_from_staked_accounts` (`runtime/src/bank.rs:2662-2677`), which subtracts `vat_to_burn_per_epoch`, leaving lamports `== rent_exempt_reserve + R`.
4. Repeat over subsequent epochs while further rewards accrue; assert via a unit/integration test that `lamports < rent_exempt_reserve + pending_delegator_rewords` becomes true, and that a subsequent `withdraw` call in `programs/vote/src/vote_state/mod.rs` (`min_balance` check at lines 1112-1121) fails to honor the full recorded `pending_delegator_rewards`, demonstrating the reserve was eroded by the VAT burn rather than by any withdrawal request.

### Citations

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

**File:** vote/src/vote_account.rs (L220-231)
```rust
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
```

**File:** runtime/src/bank.rs (L2662-2677)
```rust
        // Vote accounts have already been filtered by clone_and_filter_for_vat to only include
        // accounts with non-zero stake and sufficient balance.
        for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
            let mut account = self.get_account(vote_pubkey).unwrap();
            total_vat += vat_to_burn_per_epoch;
            account.set_lamports(
                account
                    .lamports()
                    .checked_sub(vat_to_burn_per_epoch)
                    .expect(
                        "Vote accounts should have already been filtered to contain enough \
                         balance for the VAT",
                    ),
            );
            accounts_to_store.push((*vote_pubkey, account));
        }
```
