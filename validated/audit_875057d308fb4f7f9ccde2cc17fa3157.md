## Title
`pending_delegator_rewards` is not deducted from the vote account balance snapshot used to compute per-epoch block-reward distributions, allowing the reserved delegator-reward pool to be spent through the normal withdraw path before distribution runs - ([File: programs/vote/src/vote_state/mod.rs])

## Summary
This is not a full, verified vulnerability — it is the closest structural analog I could locate in the local Agave codebase, and I could not fully confirm exploitability before running out of investigation budget. The Hyperdrive bug's core invariant is: *a fee/reserve amount that must be excluded from a fungible pool ends up commingled with that pool's spendable balance, so ordinary pool operations can drain it before the rightful owner claims it.* The Solana analog is `pending_delegator_rewards` on `VoteStateV4` (SIMD-0123): lamports deposited via `deposit_delegator_rewards` are meant to be reserved for delegators and later paid out proportionally during epoch-reward distribution (`calculate_block_reward` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), but they live inside the vote account's ordinary lamport balance alongside withdrawer-controlled funds.

## Finding Description
`deposit_delegator_rewards` transfers lamports into the vote account and increments `pending_delegator_rewards` [1](#0-0) . The `withdraw` instruction guards against the authorized withdrawer draining below `pending_delegator_rewards + rent_exempt_minimum`, reading `pending_delegator_rewards` from the *current* vote account state at withdrawal time [2](#0-1) .

Separately, at epoch-boundary reward calculation, `calculate_block_reward` computes each stake account's share of the block reward as `pending_delegator_rewards * stake / total_active_stake`, reading `pending_delegator_rewards` from a `vote_state_view` snapshot taken at calculation time [3](#0-2) . This calculation is explicitly split from account loading/mutation — the commission-account analog in the same module documents that "intervening account mutations... are reflected" only for commission accounts, deferring account loads to distribution time [4](#0-3) . I was unable to confirm, within the time available, whether the block-reward distribution path for `pending_delegator_rewards` (in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`) re-reads the live `pending_delegator_rewards` field and live vote account balance at debit time, or whether it uses the calculation-time snapshot to determine both the amount to subtract from the vote account and the field decrement.

If the distribution step subtracts a *stale, calculation-time* `pending_delegator_rewards` figure from the vote account's *live* balance/field (analogous to how the Hyperdrive `_applyCloseLong` mixes governance-fee-inclusive deltas into share reserves), then any withdrawal that changes the vote account's balance between calculation and distribution (permitted so long as it respects the *live* `pending_delegator_rewards` value at withdrawal time) could desynchronize the amount actually reserved from the amount the distribution logic expects to move to stakers, producing either under/over-payment of delegator rewards or an assertion/panic in the reward-accounting invariant checks (similar to the `assert!` in `distribute_reward_commissions`) [5](#0-4) .

## Impact Explanation
If the drift described above is real, its impact would be either (a) loss/misallocation of delegator rewards that were deposited in good faith via `deposit_delegator_rewards`, analogous to governance fees being "traded out" and becoming unclaimable in the Hyperdrive report, or (b) a bank-level panic from the reward-accounting sanity assertion, which would be a validator crash / consensus-halt-class issue. Both would be High severity if confirmed. However, this is speculative without having read the actual debit logic in `distribution.rs`.

## Likelihood Explanation
Low-to-uncertain. The `withdraw` path already contains an explicit SIMD-0123 guard reading the *live* `pending_delegator_rewards` value, which is the correct fix pattern the Hyperdrive report recommends (exclude the reserved amount from what can be spent). This suggests the developers were aware of exactly this bug class and defended against it in the withdrawal path. I could not verify whether the same live-value discipline is maintained in the distribution/debit path within the available tool budget, so I cannot assert this is an active, exploitable bug — only that it is the closest structural analog and the one place in the codebase where the exact "reserved-fee-mixed-into-spendable-balance" pattern from the report recurs.

## Recommendation
A Devin agent with full repo access should read `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` in full to confirm (1) whether it re-fetches the live `pending_delegator_rewards` field and live vote-account lamports at the point it debits the vote account for block-reward distribution, and (2) whether any sequence of `deposit_delegator_rewards`/`withdraw` calls between reward calculation and distribution can cause the debited amount to diverge from the field decrement, triggering either fund misallocation or the capitalization/lamports invariant assertions to fire.

## Proof of Concept
Not constructed — I was unable to trace the exact debit/decrement code in `distribution.rs` before the investigation budget was exhausted, so I cannot provide a concrete reproduction. This should be the first step for further investigation rather than treated as a confirmed finding.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L369-374)
```rust
        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```
