## Analysis

The report's underlying pattern is: an internal accounting field reserves/allocates funds against a contract balance, a withdrawal/recover guard checks against that reservation, and there is no code path to ever clear the reservation once its associated distribution can no longer occur — so the reserved funds become permanently unrecoverable.

In Agave's vote program (SIMD-0123 / block revenue sharing), the field `pending_delegator_rewards` on `VoteStateV4` plays the exact same "reservation" role as `totalAllocation` in the reported `SpiceAuction` contract.

### Title
Vote account `pending_delegator_rewards` reservation can permanently lock lamports when active delegated stake drops to zero before distribution - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
`pending_delegator_rewards` accrues lamports owed to a vote account's delegators from block-revenue commission via `add_pending_delegator_rewards` [1](#0-0) . The `withdraw` instruction refuses to close the vote account while this value is non‑zero, and caps withdrawable balance at `lamports - pending_delegator_rewards - rent_exempt_minimum` otherwise [2](#0-1) . The only mechanism that reduces this reservation is `calculate_block_reward`, which distributes it to stakers in proportion to `stake / total_active_stake` for that vote account during the epoch-boundary reward calculation [3](#0-2) . If `total_active_stake` for that vote account is `0` at the rewarded epoch, the function returns `0` unconditionally, meaning none of the accrued `pending_delegator_rewards` is ever paid out or cleared [4](#0-3) .

### Finding Description
`pending_delegator_rewards` mirrors `totalAllocation` in the reported bug: it is a liability counter that gates a withdrawal function (`withdraw`), but nothing in the codebase decrements it independent of `calculate_block_reward` successfully attributing a non-zero share to some staker. `calculate_block_reward` is keyed entirely by `total_active_stake`, sourced from `reward_epoch_delegated_stakes.delegated_stakes.get(&vote_pubkey)` [5](#0-4) . This value reflects active stake delegated to the vote account during the *rewarded* epoch — an unprivileged staker action (deactivating/withdrawing stake) fully controls it. If every staker delegated to a vote account undelegates before/at the point the reward is computed for that epoch (or the vote account accrues block-revenue commission in an epoch where it had no delegated active stake at all, e.g., right after creation or after all stake deactivates), `total_active_stake == 0` for that account, and `calculate_block_reward` returns `0` for every delegation, leaving the vote account's `pending_delegator_rewards` field untouched forever.

The `withdraw` guard then permanently blocks the authorized withdrawer: it cannot close the account (`pending_delegator_rewards > 0` check at [6](#0-5) ) nor withdraw below `rent_exempt_minimum + pending_delegator_rewards` [7](#0-6) . There is no recovery/sweep instruction elsewhere in the vote program to zero out or reclaim a `pending_delegator_rewards` balance that can no longer be distributed — exactly the missing "recover" mechanism the external report flags for `SpiceAuction`.

### Impact Explanation
Lamports equal to the un-distributable `pending_delegator_rewards` become permanently locked in the vote account, inaccessible to the authorized withdrawer, with no on-chain recovery path. This is a direct, unprivileged fund-loss condition triggered by ordinary staker behavior (mass undelegation), not by any malicious/trusted actor assumption.

### Likelihood Explanation
Requires SIMD-0123 block-revenue-sharing to be active and a vote account whose delegated active stake reaches zero in a rewarded epoch while it still holds unpaid `pending_delegator_rewards` (e.g., all delegators withdraw stake in the same epoch, or the vote account earns block-revenue commission before receiving any stake delegation). This is a plausible, non-adversarial edge case for small/newly created validators or during large-scale stake churn, though I could not fully trace every call site that invokes `add_pending_delegator_rewards` (i.e., confirm all conditions under which the field is incremented) within the available iterations, so the exact accrual triggers should be verified against `fee_distribution.rs` / block-revenue commission handling before treating this as fully confirmed.

### Recommendation
Add a mechanism to either (a) redistribute or reclaim `pending_delegator_rewards` to the authorized withdrawer (or burn/re-route to the collector) when `total_active_stake` for the vote account is `0` at distribution time, or (b) prevent the reservation from growing when there is no active stake to eventually receive it.

### Proof of Concept
1. Create a vote account, delegate stake to it, and allow it to accrue `pending_delegator_rewards` via block-revenue commission over one or more epochs (`add_pending_delegator_rewards`) [1](#0-0) .
2. Have all stakers deactivate and fully withdraw their delegated stake so that, by the epoch boundary used for reward calculation, `reward_epoch_delegated_stakes.delegated_stakes` has no entry (or a `0` entry) for this vote account.
3. At the epoch boundary, `calculate_block_reward` returns `0` for this vote account regardless of the size of `pending_delegator_rewards` [4](#0-3) , so `pending_delegator_rewards` remains unchanged.
4. Call `Withdraw` with `lamports` set to withdraw the full remaining balance: the instruction fails with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0` blocks closing the account, per the check at [6](#0-5)  (behavior exercised by the existing test `test_withdraw_pending_delegator_rewards` [8](#0-7) , but that test always reduces `pending_delegator_rewards` back to `0` manually rather than through the on-chain distribution path — demonstrating there is no in-protocol way to clear it once distribution can't occur).

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
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

**File:** programs/vote/src/vote_processor.rs (L5264-5282)
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

        // Should fail, can't withdraw more than
        // (lamports - pending_delegator_rewards - rent_exempt).
        process_instruction(
            features,
            &serialize(&VoteInstruction::Withdraw(vote_account_lamports + 1)).unwrap(),
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Err(InstructionError::InsufficientFunds),
        );
```
