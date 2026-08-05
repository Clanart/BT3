## Analysis

The Lido report's broken invariant: **an unprivileged address can push value into a contract that later feeds a ratio/share-price calculation, with no way for the protocol to distinguish "legitimate" from "attacker-injected" value.**

The closest real analog in this Agave codebase is the SIMD-0123 `DepositDelegatorRewards` vote instruction, which lets **any signer, with no relationship to the vote account's validator identity or withdraw authority**, push lamports into a vote account and increment `pending_delegator_rewards` — a value that (a) is later used as the numerator in a per-stake-account payout ratio, and (b) is used as a mandatory minimum reserve that blocks the vote account's legitimate withdrawer from withdrawing/closing the account.

### Finding Description

`deposit_delegator_rewards` requires only that the *source* account sign the transfer — it performs no check that the depositor is the vote account's authorized withdrawer, node identity, or designated commission collector: [1](#0-0) 

It then CPIs a System transfer into the vote account and unconditionally increments `pending_delegator_rewards` via `add_pending_delegator_rewards`, which only checks for `u64` overflow, not authorization or a maximum bound: [2](#0-1) [3](#0-2) 

`pending_delegator_rewards` is later read directly out of the vote account state and used as the numerator of a proportional payout calculation (`calculate_block_reward`), dividing it among stake delegations by `stake / total_active_stake`: [4](#0-3) 

Separately (and more directly analogous to the Lido bug's "locks up value that shouldn't be locked" pattern), `withdraw()` treats `pending_delegator_rewards` as a mandatory reserve that the account's own authorized withdrawer cannot touch: [5](#0-4) 

Because any unrelated signer can call `DepositDelegatorRewards` with an arbitrarily small deposit and grow `pending_delegator_rewards` without limit (bounded only by `u64::MAX`), an attacker with no relationship to the vote account can:
1. Force additional lamports to be permanently locked in someone else's vote account (raising `min_balance` in `withdraw()` above what the legitimate withdrawer intended), and
2. Force the vote account to be **un-closeable** (`withdraw()` rejects a full-balance withdrawal whenever `pending_delegator_rewards > 0`), even against the withdrawer's wishes: [6](#0-5) 

This is the exact bug-class match to the Lido report: "it is possible to send [value] to this contract from any address," which then feeds downstream accounting (share price there; per-stake payout ratio and withdrawal-reserve calculation here) that assumes the deposited value came from an authorized/intended source.

### Impact Explanation

The attack does not steal funds outright (the attacker's own lamports are spent and ultimately redistributed as rewards to the vote account's delegators), but it does cause an **unauthorized reduction of the vote account owner's control over their own funds** — the authorized withdrawer can be griefed into having part of their balance permanently reserved/locked and their vote account rendered non-closeable, purely by a third party's unsolicited deposit. This qualifies as fund lock/loss of control for an unprivileged party against another account's funds, within the "accounts/runtime" category of valid impact.

### Likelihood Explanation

Likelihood is high in terms of feasibility (any account can sign a tiny SOL transfer and call the instruction against any V4 vote account; cost is proportional to the attacker's own deposit, which is fully refunded to delegators, not burned), but the instruction is gated behind three simultaneous features (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing`) that must all be active: [7](#0-6) 
so the exposure only exists once SIMD-0123/0232/0291 are fully activated on a cluster.

### Recommendation

`deposit_delegator_rewards` should either (a) restrict who may initiate the deposit (e.g., require the vote account's authorized withdrawer or a designated block-revenue collector to co-sign, mirroring `update_commission_collector`'s `verify_authorized_signer` pattern), or (b) decouple the withdrawal-reserve guarantee in `withdraw()` from a value that any outside party can inflate — e.g., cap per-call/per-epoch deposit growth, or only honor `pending_delegator_rewards` contributed through an authorized collector path.

### Proof of Concept

Existing unit tests already demonstrate the unrestricted deposit and its interaction with withdrawal: [8](#0-7) [9](#0-8) 

Sequence:
1. Attacker (unrelated to the target vote account) constructs `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with itself as the signing `source_address` and the victim's vote account as `vote_account_index`.
2. Instruction succeeds without any authorization check on the depositor — only `verify_authorized_signer(&source_address, signers)` (i.e., the attacker signing their own transfer), per `deposit_delegator_rewards`.
3. `pending_delegator_rewards` in the victim's vote account increases by the deposited amount.
4. The victim's authorized withdrawer subsequently calls `Withdraw`; per `withdraw()`, the withdrawable amount is reduced by the attacker-inflated `pending_delegator_rewards`, and if the withdrawer tries to close the account (`remaining_balance == 0`), the call now fails with `InstructionError::InsufficientFunds` due solely to the attacker's uninvited deposit.

**Uncertainty**: I could not find code that decrements/clears `pending_delegator_rewards` after `calculate_block_reward`/distribution completes within the files I was able to inspect (`distribution.rs`, `calculation.rs`); if such a reset does not exist, the lock described above would be permanent rather than resolved at the next epoch boundary. This detail should be verified directly in the full repository (e.g., in `distribute_reward_commissions` or the block-reward distribution path) before treating the "temporary vs. permanent lock" severity as settled — a Devin session with full repo/terminal access would be needed to confirm this definitively.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-231)
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

**File:** programs/vote/src/vote_processor.rs (L5120-5141)
```rust
        // Vote account should have been credited `deposit_amount`.
        // Source account should have been debited `deposit_amount`.
        // Vote state's `pending_delegator_rewards` should be updated.
        let vote_account_starting_lamports = vote_account_v4.lamports();
        let source_account_starting_lamports = source_lamports;
        let resulting_vote_account = &resulting_accounts[0];
        let resulting_source_account = &resulting_accounts[1];
        let vote_state =
            deserialize_vote_state_for_test(resulting_vote_account.data(), &vote_pubkey);
        assert_eq!(
            resulting_vote_account.lamports(),
            vote_account_starting_lamports + deposit_amount,
        );
        assert_eq!(
            resulting_source_account.lamports(),
            source_account_starting_lamports - deposit_amount,
        );
        assert_eq!(
            vote_state.as_ref_v4().pending_delegator_rewards,
            deposit_amount,
        );

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
