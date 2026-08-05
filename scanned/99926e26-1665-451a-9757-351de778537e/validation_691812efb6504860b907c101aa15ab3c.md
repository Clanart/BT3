Given the tool budget is exhausted, here is my analysis with an explicit note on what remains unverified.

### Title
`pending_delegator_rewards` in Vote State v4 is never decremented after block-reward distribution, causing repeated over-distribution / stuck-withdrawal accounting divergence - (File: `programs/vote/src/vote_state/handler.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The Sherlock/Velar bug is a class of "unbounded virtual accrual that is never reconciled against the real backing balance," which eventually diverges from reality and either locks funds (underflow on close) or lets a party extract more than what was actually deposited. The closest Agave analog is the SIMD-0123 `pending_delegator_rewards` field on `VoteStateV4`: it is only ever *increased* (`add_pending_delegator_rewards`, guarded by `checked_add`) via the unprivileged `DepositDelegatorRewards` instruction, and it is read every epoch by `calculate_block_reward` to compute the pro-rata share paid to each delegator's stake account. I could not find any code path in this repository snapshot that decreases/zeroes `pending_delegator_rewards` after it has been used to compute a distribution.

### Finding Description
- Anyone can call `DepositDelegatorRewards` to transfer lamports into a vote account and atomically call `add_pending_delegator_rewards`, which does `checked_add` on `VoteStateV4::pending_delegator_rewards`: [1](#0-0) , invoked from [2](#0-1) .
- Every epoch, `calculate_block_reward` reads the vote account's current `pending_delegator_rewards` and computes each stake account's share as `pending_delegator_rewards * stake / total_active_stake` (clamped to `pending_delegator_rewards`): [3](#0-2) .
- That computed `block_reward` is then unconditionally minted onto the stake account via `checked_add_lamports`, with no corresponding write that reduces the *vote account's* `pending_delegator_rewards` value: [4](#0-3) .
- A repo-wide search for any mutation that decrements this field (`checked_sub`, `-=`, a setter such as `sub_pending_delegator_rewards`) returned no matches outside of the `checked_add` in `add_pending_delegator_rewards`.
- Meanwhile, `withdraw()` treats `pending_delegator_rewards` as principal still owed to delegators and forbids withdrawing below `min_rent_exempt_balance + pending_delegator_rewards`, and forbids closing the account at all while `pending_delegator_rewards > 0`: [5](#0-4) .

If `pending_delegator_rewards` is never reduced after being paid out, the same deposited amount is used as the reward basis every subsequent epoch, meaning stakers are paid repeatedly against a balance that was already distributed once (or the vote account is permanently prevented from being closed/fully withdrawn because the withdraw guard sees a "debt" that in reality has already been paid).

### Impact Explanation
If confirmed, this breaks fund accounting invariants in two ways: (1) validators/delegators could receive block-reward payouts far in excess of what was actually deposited via `DepositDelegatorRewards`, effectively minting/duplicating rewards each epoch (fund theft / false execution of runtime reward accounting), and (2) the vote account's authorized withdrawer is permanently blocked from withdrawing/closing the account because `pending_delegator_rewards` never reaches zero even though the "debt" was already paid out, mirroring the Velar bug's "position can never be closed" outcome.

### Likelihood Explanation
Medium-to-low confidence: this requires SIMD-0123/SIMD-0185/SIMD-0291/SIMD-0232 features to be active (v4 vote accounts, commission-in-bps, custom commission collector, block revenue sharing), and it depends on my inability to locate a decrement path within the indexed portion of the codebase, which is a negative-evidence conclusion. I was not able to fully trace `distribute_reward_commissions`, `store_stake_accounts_in_partition`'s handling of the *vote* account (as opposed to the *stake* account), or any vote-account-side bookkeeping update that might reset `pending_delegator_rewards` per epoch, due to exhausting the available tool-call budget.

### Recommendation
A background Devin agent should:
1. Fully trace `redeem_delegation_rewards` / `distribute_reward_commissions` / `store_stake_accounts_in_partition` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` and `distribution.rs` to confirm whether the *vote account's* `pending_delegator_rewards` field (not just the stake account) is ever rewritten/decremented after a distribution epoch.
2. If no decrement exists, add logic to subtract the total `block_reward` amount actually distributed for a vote account from its `pending_delegator_rewards` in the same partitioned-distribution pass that mints the stake-account lamports, keeping the two "sides" of the accounting (vote-account records the promise, stake accounts receive the payout) in sync.
3. Add an invariant test asserting that after N consecutive epochs, cumulative `block_reward` paid out for a vote account does not exceed the total amount ever deposited via `DepositDelegatorRewards` for that vote account.
4. Re-verify the `withdraw()` guard in `programs/vote/src/vote_state/mod.rs` continues to correctly reflect `pending_delegator_rewards` after the fix, so withdrawer is not indefinitely blocked once the pending amount has genuinely been paid out.

### Proof of Concept
Not independently reproduced in a test harness due to tool budget exhaustion. The suggested reproduction path for the background agent:
1. Enable `commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing` features.
2. Create a `VoteStateV4` account with a validator identity and one delegator stake account.
3. Call `DepositDelegatorRewards` once with `deposit = D` lamports.
4. Advance through several reward epochs and use the test helper `get_block_reward_for_test` pattern (seen in [6](#0-5) ) to compute `calculate_block_reward` for each epoch without any intervening decrement of `pending_delegator_rewards`.
5. Assert whether the sum of `block_reward` paid across epochs 1..N exceeds `D` — if it does, the vulnerability is confirmed.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L188-231)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4246-4318)
```rust
    fn get_block_reward_for_test(
        individual_stake: u64,
        total_stake: u64,
        pending_delegator_rewards: u64,
        rewarded_epoch: u64,
    ) -> u64 {
        let voter_pubkey = Pubkey::new_unique();
        let vote_account = {
            let identity = Keypair::new();
            let bls_keypair =
                BLSKeypair::derive_from_signer(&identity, BLS_KEYPAIR_DERIVE_SEED).unwrap();
            let (bls_pubkey, bls_pop) = create_bls_proof_of_possession(&voter_pubkey, &bls_keypair);
            let vote_init = VoteInitV2 {
                node_pubkey: identity.pubkey(),
                authorized_voter: identity.pubkey(),
                authorized_voter_bls_pubkey: bls_pubkey,
                authorized_voter_bls_proof_of_possession: bls_pop,
                ..VoteInitV2::default()
            };
            let mut vote_state = VoteStateV4::new(
                &vote_init,
                &voter_pubkey,
                &identity.pubkey(),
                &Clock::default(),
            );
            vote_state.pending_delegator_rewards = pending_delegator_rewards;
            let mut account = solana_account::AccountSharedData::new(
                1_000_000_000,
                VoteStateV4::size_of(),
                &solana_vote_program::id(),
            );
            account
                .serialize_data(&VoteStateVersions::new_v4(vote_state))
                .unwrap();
            VoteAccount::try_from(account).unwrap()
        };

        let vote_accounts = [(voter_pubkey, (total_stake, vote_account))]
            .into_iter()
            .collect();
        let ag_epoch_type = AlpenglowEpochType::Alpenglow {
            migration_epoch: 0,
            reward_epoch_delegated_stakes: RewardEpochDelegatedStakes {
                epoch: rewarded_epoch,
                delegated_stakes: [(voter_pubkey, total_stake)].into_iter().collect(),
            },
        };

        let delegation = Delegation {
            voter_pubkey,
            stake: individual_stake,
            activation_epoch: u64::MAX, // boostrap stake so it's fully active
            ..Default::default()
        };

        let mut stake_history = StakeHistory::default();
        for epoch in 0..=rewarded_epoch {
            stake_history.add(epoch, StakeHistoryEntry::with_effective(total_stake));
        }

        let use_fixed_point_stake_math = true;
        let new_warmup_cooldown_rate_epoch = Some(0);

        calculate_block_reward(
            rewarded_epoch,
            &delegation,
            &stake_history,
            &vote_accounts,
            &ag_epoch_type,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        )
    }
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
