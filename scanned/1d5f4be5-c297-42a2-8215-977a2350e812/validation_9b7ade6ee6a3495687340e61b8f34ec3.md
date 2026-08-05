### Title
`pending_delegator_rewards` deposited into a vote account with zero delegated stake becomes permanently undistributable and cannot be withdrawn - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`DepositDelegatorRewards` (SIMD-0123) lets anyone transfer lamports into a vote account and increment its `pending_delegator_rewards` counter at any time, independent of whether the vote account currently has any active delegated stake. Distribution of that pool only happens in `calculate_block_reward`, which is invoked per stake-*delegation* at epoch boundary and explicitly returns `0` whenever `total_active_stake == 0` for that vote account. Simultaneously, `withdraw()` permanently reserves `pending_delegator_rewards` lamports and rejects withdrawal of that amount and rejects account closure while `pending_delegator_rewards > 0`. If a vote account accumulates `pending_delegator_rewards` while it has no active stake delegated to it (e.g. it is newly created, or all its stake has deactivated/cooled down to zero), there is no code path that ever reduces `pending_delegator_rewards` or distributes it, and the funds become permanently locked in the vote account — mirroring the Topolia LP-staking bug where reward accrual is decoupled from the presence of a staker/checkpoint.

### Finding Description
`pending_delegator_rewards` is a lamport counter on `VoteStateV4` that is incremented by anyone via `deposit_delegator_rewards`/`DepositDelegatorRewards`: [1](#0-0) 
This deposit has no precondition on the vote account having any active stake delegated to it — it succeeds for a brand-new vote account or a vote account whose delegators have fully deactivated.

The only mechanism that reduces this pool is `calculate_block_reward`, called once per epoch, per stake *delegation* that currently exists: [2](#0-1) 
Critically, when `total_active_stake == 0` for the vote account (looked up from `reward_epoch_delegated_stakes.delegated_stakes`), the function returns `0` unconditionally — line 211-212. Because the reward loop only iterates over `stake_delegations` that already exist in `StakesCache`, if there is no delegation to the vote account at all, this function is never even invoked for that voter, and the accumulated `pending_delegator_rewards` value is left completely untouched.

Meanwhile, `withdraw()` treats `pending_delegator_rewards` as a permanently reserved balance: [3](#0-2) 
It blocks full account closure while `pending_delegator_rewards > 0` (line 1090-1092) and caps partial withdrawals to `lamports - pending_delegator_rewards - rent_exempt_minimum` (line 1113-1121). There is no instruction in the vote program that clears or refunds `pending_delegator_rewards` independent of the epoch-boundary distribution path.

This is the direct analog of the Topolia bug: a reward pool ("rewardsPeriod"/`pending_delegator_rewards`) can be funded ("setRewards"/`DepositDelegatorRewards`) before any staker/delegation is registered against it, and the only redemption path (`stake`/`calculate_block_reward`) is gated on the existence of a stake checkpoint at the moment rewards are computed. If that checkpoint (active delegated stake) is zero at every future distribution point, the funds are stuck forever with no recovery mechanism — matching the report's "rewards can possibly be left stuck in contract."

### Impact Explanation
Lamports deposited via `DepositDelegatorRewards` into a vote account lacking active delegated stake (freshly created validator identity, or a validator whose entire stake has deactivated/cooled down within the same reward epoch and stays undelegated) become permanently unreachable: they can never be distributed (block-reward calculation always yields 0 for that voter) and can never be withdrawn (the withdraw instruction reserves exactly that amount indefinitely). This is a fund-loss condition reachable without any malicious/trusted-party assumption — it can happen from ordinary validator lifecycle events (e.g., a validator deactivates stake right before rewards can be redeemed, or deposits are made to a vote account before it has attracted any stake).

### Likelihood Explanation
This requires the `block_revenue_sharing`, `custom_commission_collector`, and `commission_rate_in_basis_points` features (SIMD-0123/0232/0291) to be active and Alpenglow enabled, since `calculate_block_reward` only runs under `AlpenglowEpochType::Alpenglow`/`MigrationEpoch`. `DepositDelegatorRewards` itself has no check requiring the target vote account to have delegated stake, so triggering the deposit side is trivial and permissionless. The stuck-funds condition further requires the vote account's active delegated stake to be zero at the relevant epoch boundary, which is a realistic and not-unlikely validator-lifecycle scenario (new vote accounts, or full stake deactivation).

### Recommendation
Either (a) reject/refuse `DepositDelegatorRewards` deposits when a vote account has zero active delegated stake at the relevant epoch, or (b) add a recovery/refund path so `pending_delegator_rewards` can be withdrawn by the authorized withdrawer once it is confirmed there is no delegated stake and none is forthcoming (analogous to enforcing "at least one stake before rewards start" in the original report), or (c) roll unpaid `pending_delegator_rewards` back to the depositor/authorized withdrawer instead of leaving it permanently reserved when distribution repeatedly nets zero.

### Proof of Concept
1. Enable `block_revenue_sharing`, `custom_commission_collector`, `commission_rate_in_basis_points`, and Alpenglow features.
2. Create a new `VoteStateV4` account with no stake ever delegated to it (`total_active_stake == 0` in `RewardEpochDelegatedStakes`).
3. Call `VoteInstruction::DepositDelegatorRewards { deposit: N }` from any signer with lamports; this succeeds and sets `pending_delegator_rewards = N` — [4](#0-3) .
4. Advance through an epoch boundary. During reward calculation, since there is no stake delegation to this vote account, `calculate_block_reward` is never invoked for it (no matching entries in `stake_delegations`), so `pending_delegator_rewards` remains `N` — confirmed by the unit test showing `calculate_block_reward` returns `0` whenever `total_active_stake == 0`: [5](#0-4) .
5. Attempt to withdraw the full vote account balance: the `Withdraw` instruction fails with `InsufficientFunds` because `pending_delegator_rewards > 0` blocks full closure and caps partial withdrawal below that reserve — [6](#0-5) , as also demonstrated by `test_withdraw_pending_delegator_rewards`: [7](#0-6) .
6. `N` lamports remain permanently locked in the vote account with no code path to redeem or withdraw them.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L935-988)
```rust
/// Deposit delegator rewards into a vote account (SIMD-0123).
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

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4320-4332)
```rust
    #[test]
    fn test_calculate_block_reward_specific() {
        // get nothing
        assert_eq!(get_block_reward_for_test(0, 0, 0, 0), 0);
        // get everything
        assert_eq!(get_block_reward_for_test(1, 1, 1, 0), 1);
        // individual stake higher than block reward, capped
        assert_eq!(get_block_reward_for_test(2, 1, 1, 0), 1);
        // not truncated
        assert_eq!(get_block_reward_for_test(1, 10, 10, 0), 1);
        // truncated
        assert_eq!(get_block_reward_for_test(1, 10, 9, 0), 0);
    }
```

**File:** programs/vote/src/vote_processor.rs (L5219-5282)
```rust
    #[test]
    #[allow(clippy::arithmetic_side_effects)]
    fn test_withdraw_pending_delegator_rewards() {
        let rent_sysvar = Rent::default();
        let rent_minimum_balance = rent_sysvar.minimum_balance(VoteStateV4::size_of());

        let pending_rewards = 500_000;
        let extra_for_withdraw = 100_000;
        let vote_account_lamports = rent_minimum_balance + pending_rewards + extra_for_withdraw;

        let (vote_pubkey, _authorized_voter, authorized_withdrawer, mut vote_account) =
            create_test_account_with_authorized();

        // Set some pending delegator rewards.
        {
            let mut vote_state =
                VoteStateV4::deserialize(vote_account.data(), &vote_pubkey).unwrap();
            vote_state.pending_delegator_rewards = pending_rewards;
            vote_account.set_data_from_slice(&VoteStateHandler::new_v4(vote_state).serialize());
            vote_account.set_lamports(vote_account_lamports);
        };

        let features = VoteProgramFeatures::all_enabled();

        let instruction_accounts = vec![
            AccountMeta {
                pubkey: vote_pubkey,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: authorized_withdrawer,
                is_signer: true,
                is_writable: true,
            },
        ];

        let rent_account = account::create_account_shared_data_for_test(&rent_sysvar);
        let transaction_accounts = vec![
            (vote_pubkey, vote_account.clone()),
            (authorized_withdrawer, AccountSharedData::default()),
            (sysvar::clock::id(), create_default_clock_account()),
            (sysvar::rent::id(), rent_account.clone()),
        ];

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
