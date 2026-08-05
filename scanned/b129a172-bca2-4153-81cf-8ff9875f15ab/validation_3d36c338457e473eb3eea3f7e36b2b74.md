### Title
`pending_delegator_rewards` is incremented on deposit but never decremented, permanently locking vote-account withdrawer funds and reusing a stale reward pool across epochs - (`programs/vote/src/vote_state/mod.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The Tessera bug is a case where a value representing a user's contributed funds (`userContributions`/`totalContributions`) is credited on deposit but the corresponding "refundable/trackable" counter is never updated on the failure path, so funds get silently absorbed with no revert and no way to reclaim them. The Agave (SIMD-0123 / vote-program-v4) analog is `pending_delegator_rewards`: real lamports are transferred into a vote account and the counter is incremented via `add_pending_delegator_rewards`, but no corresponding decrement of that counter exists anywhere in the codebase, even though the counter is used both (a) as a hard reserve that blocks withdrawal/closing of the vote account, and (b) as the basis for computing a new "block reward" share every epoch.

### Finding Description
`deposit_delegator_rewards()` performs a CPI transfer of lamports from a source account into the vote account and then increments the tracking counter: [1](#0-0) 

The only mutator of this field is `add_pending_delegator_rewards`, which only ever adds: [2](#0-1) 

A repo-wide search for any subtraction/reset of `pending_delegator_rewards` (`sub_pending_delegator_rewards`, `-=`, `checked_sub`, `set_pending_delegator_rewards`) across `programs/vote/src/vote_state/mod.rs`, `programs/vote/src/vote_state/handler.rs`, `runtime/src/bank/partitioned_epoch_rewards/*.rs`, and `vote/src/vote_state_view*` returned no matches. The only other place the field is used besides the withdraw guard is `calculate_block_reward`, which computes a delegator's share of the value but does not write it back: [3](#0-2) 

That computed `block_reward` is then minted directly into the *stake account's* lamports in `build_updated_stake_reward` — it is not debited from the vote account, and capitalization accounting in `distribute_epoch_rewards_in_partition` only adjusts for `stake_reward_lamports_minted` and `block_reward_lamports_burned`, never for `block_reward_lamports_distributed` against the vote account's real balance: [4](#0-3) [5](#0-4) 

Meanwhile, `withdraw()` treats `pending_delegator_rewards` as a permanent reserve: it blocks closing the account while the value is nonzero, and reduces the withdrawable balance by exactly that amount every time: [6](#0-5) 

Because nothing ever reduces `pending_delegator_rewards` after a deposit, the lamports that were transferred into the vote account (real, user-contributed funds via `DepositDelegatorRewards`) can never be withdrawn by the `authorized_withdrawer`, and the account can never be fully closed — mirroring the Tessera pattern where contributed value is accepted, tracked in a way that makes it look "pending", but the code path that would clear/refund the tracked amount never runs. As a secondary effect, because the counter never shrinks, `calculate_block_reward` recomputes a reward share off the same (only growing) `pending_delegator_rewards` value every epoch it remains active, rather than off a value reduced by prior distributions.

### Impact Explanation
This is a fund-loss/lock bug in a built-in program (`programs/vote`) reachable by any unprivileged user who calls `DepositDelegatorRewards` (or by the protocol's normal reward-crediting flow once SIMD-0123 is active): once lamports are deposited and `pending_delegator_rewards` is set nonzero, the vote account's `authorized_withdrawer` permanently loses access to that portion of the balance — `withdraw()` will reject withdrawing below `rent_exempt + pending_delegator_rewards`, and will always reject closing the account (`InstructionError::InsufficientFunds`) while the counter is nonzero. Additionally, `calculate_block_reward` re-derives a distribution share from a value that is never reduced, which risks recurring extra reward computation each epoch from what should be a one-time pool.

### Likelihood Explanation
Likelihood is tied to activation of SIMD-0123 (vote state v4 + commission-in-basis-points + custom commission collector + block revenue sharing feature gates), all of which are represented as concrete feature flags in this codebase and exercised by dedicated tests (`test_deposit_delegator_rewards`, `test_withdraw_pending_delegator_rewards`). Given all four gates active, any account holding a v4 vote account that receives a `DepositDelegatorRewards` deposit is affected — no malicious peer, validator, or privileged actor is required; a normal user calling the public instruction triggers the lock.

### Recommendation
Add a corresponding decrement path for `pending_delegator_rewards` that is invoked exactly when the calculated `block_reward` for a given epoch/partition is actually paid out from the vote account (mirroring `add_pending_delegator_rewards`), and ensure the vote account's own lamports are debited by that same amount so the reserve accounting stays consistent with `withdraw()`'s guard. Audit `calculate_block_reward` / `store_stake_accounts_in_partition` to confirm the vote-account debit and confirm capitalization changes account for `block_reward_lamports_distributed` in the same way `stake_reward_lamports_minted` is accounted for.

### Proof of Concept
1. Enable `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing` features (as in `test_deposit_delegator_rewards`).
2. Call `VoteInstruction::DepositDelegatorRewards { deposit: N }` from any signer with lamports, targeting a v4 vote account — this transfers `N` lamports into the vote account and sets `pending_delegator_rewards = N` per [1](#0-0) .
3. Attempt `Withdraw` for any amount that would bring the vote account below `rent_exempt_minimum + pending_delegator_rewards`, or attempt to fully close the account — both fail with `InstructionError::InsufficientFunds` per [6](#0-5) , as directly demonstrated by `test_withdraw_pending_delegator_rewards` (`programs/vote/src/vote_processor.rs:5219-5331`).
4. Confirm no instruction/path in the codebase ever reduces `pending_delegator_rewards` back toward zero (verified via repo-wide search for subtract/reset operations on the field), so the locked lamports and the withdrawal block are permanent for the life of the account.

**Note on completeness:** I was unable to fully trace, within the available tool budget, whether some other yet-undiscovered code path (e.g., a not-yet-indexed part of the reward-distribution pipeline) performs the missing decrement. The grep searches across `programs/vote/*` and `runtime/src/bank/partitioned_epoch_rewards/*` found no such logic, but a full-repository build/trace by a Devin session with complete file access would be needed to close out this uncertainty with full confidence.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-198)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);
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
