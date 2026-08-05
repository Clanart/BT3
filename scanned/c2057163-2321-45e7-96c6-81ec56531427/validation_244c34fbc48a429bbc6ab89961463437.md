## Analysis

The external report's bug class is: *a liability (pending redemption) is tracked in a separate accounting field but never subtracted from the balance/threshold checks that gate withdrawals or other balance-reducing operations, letting other operations shrink the pool below what the recorded liability requires.*

The closest real analog in this Agave codebase is in the **SIMD-0123 delegator-rewards / SIMD-0357 Validator Admission Ticket (VAT) burn** interaction between `programs/vote/src/vote_state/mod.rs` and `runtime/src/bank.rs`.

### The invariant that exists
`vote_state::withdraw()` deliberately protects delegator-owed funds: any withdrawal (or full account close) is rejected unless the vote account keeps at least `rent_exempt_minimum + pending_delegator_rewards` lamports: [1](#0-0) 

`pending_delegator_rewards` is a liability that grows via `deposit_delegator_rewards`/`add_pending_delegator_rewards`, representing lamports already sitting in the vote account's balance that are earmarked for stakers: [2](#0-1) [3](#0-2) 

### The invariant that is broken
The bank-level VAT burn (`maybe_burn_vat_from_staked_accounts`), which runs automatically every epoch under Alpenglow, subtracts `vat_to_burn_per_epoch` lamports directly from every top-staked vote account's balance, bypassing `vote_state::withdraw()` entirely: [4](#0-3) 

The only guard before this burn is the admission filter (`clone_and_filter_for_vat`), which checks `vote_account.lamports() >= minimum_vote_account_balance`: [5](#0-4) 

And `minimum_vote_account_balance_for_vat()` computes that threshold as only `rent_exempt_minimum (+ vat_to_burn_per_epoch)` — it never adds `pending_delegator_rewards`: [6](#0-5) 

So a vote account holding, e.g., `rent_exempt_minimum + vat_to_burn_per_epoch + 1` lamports with a large `pending_delegator_rewards` balance (deposited via `DepositDelegatorRewards`) passes the VAT admission filter and then has `vat_to_burn_per_epoch` unconditionally subtracted, driving its lamports below `rent_exempt_minimum + pending_delegator_rewards` — the exact reserve that `withdraw()` treats as untouchable. The burned lamports go to the incinerator, permanently destroying funds that were reserved for delegators, not the vote-account owner's own spendable balance.

I could not find any code path that decrements `pending_delegator_rewards` when the corresponding lamports are consumed (no `checked_sub` on the field), so this liability tracking has no counterbalancing mechanism once VAT burns into it — the invariant is silently violated rather than merely delayed.

### Title
Validator Admission Ticket burn ignores `pending_delegator_rewards`, allowing bank-level operations to burn delegator-owed lamports below the `withdraw()`-enforced reserve - ([File: runtime/src/bank.rs])

### Summary
`vote_state::withdraw()` enforces that a vote account can never spend below `rent_exempt_minimum + pending_delegator_rewards`, treating deposited delegator rewards as untouchable reserved funds [7](#0-6) . The SIMD-0357 VAT admission filter and burn routine only guard against `rent_exempt_minimum + vat_to_burn_per_epoch` [8](#0-7) , and unconditionally subtracts `vat_to_burn_per_epoch` from every admitted vote account's lamports every epoch [9](#0-8) , with no awareness of `pending_delegator_rewards`.

### Finding Description
`pending_delegator_rewards` is a per-vote-account liability field introduced by SIMD-0123 to track lamports already deposited into the vote account and owed to delegators via block-revenue sharing [10](#0-9) . The only code that protects this reserve from being spent is the explicit `withdraw()` instruction check. Any other lamport-reducing operation on the vote account that bypasses `vote_state::withdraw()` is free to consume those reserved lamports.

`maybe_burn_vat_from_staked_accounts` is exactly such an operation: it runs at the epoch boundary for every vote account selected by `clone_and_filter_for_vat`, and does a raw `checked_sub`/`set_lamports` on the account outside of the vote program entirely [9](#0-8) . The admission threshold used to select "safe" accounts, `minimum_vote_account_balance_for_vat`, is computed purely from rent-exemption and the VAT burn amount, never incorporating `pending_delegator_rewards` [6](#0-5) .

Consequently, a vote account can simultaneously:
- Hold `pending_delegator_rewards = X` (large, from `DepositDelegatorRewards`), and
- Hold total lamports just above `rent_exempt_minimum + vat_to_burn_per_epoch` (satisfying VAT admission),

and still be selected for VAT burn, which removes `vat_to_burn_per_epoch` lamports unconditionally into the incinerator [11](#0-10) , dropping the account's balance below `rent_exempt_minimum + pending_delegator_rewards`. This is precisely the value `withdraw()` treats as inviolable. Existing guards do not stop this path because:
1. `clone_and_filter_for_vat`'s `has_balance` check never reads `pending_delegator_rewards` [12](#0-11) .
2. The burn itself uses `.expect()` only to guard against arithmetic underflow relative to the (incomplete) admission threshold, not against violating the delegator reserve [13](#0-12) .
3. No code was found that decrements `pending_delegator_rewards` to reconcile it with the reduced balance, so the liability now silently exceeds what the account can honor.

### Impact Explanation
This causes fund loss for delegators: lamports that were explicitly deposited and recorded as owed to stakers (`pending_delegator_rewards`) can be irreversibly burned to the incinerator by an unrelated protocol mechanism (VAT), instead of being protected the way `withdraw()` protects them. This directly undermines the accounting invariant the vote program itself enforces, and does so as normal, non-malicious protocol operation (VAT burn runs every epoch for every admitted validator once Alpenglow is active) rather than requiring any attacker action.

### Likelihood Explanation
Likelihood is data-dependent but not contrived: it triggers whenever a vote account's lamport balance sits between `minimum_vote_account_balance_for_vat()` and `minimum_vote_account_balance_for_vat() + pending_delegator_rewards`, a range validator operators can easily land in since `DepositDelegatorRewards` is a normal, expected instruction under block-revenue sharing and vote-account operators are not required to maintain extra headroom for it. No feature flag or admin action is needed beyond Alpenglow being active, which is the target production configuration for this reward mechanism.

### Recommendation
Include `pending_delegator_rewards` in `minimum_vote_account_balance_for_vat()` (and correspondingly in the VAT admission filter), or explicitly clamp/skip the VAT burn amount so it never reduces a vote account's lamports below `rent_exempt_minimum + pending_delegator_rewards`, mirroring the reserve check already implemented in `vote_state::withdraw()`.

### Proof of Concept
1. Vote account `V` (Alpenglow active) has `pending_delegator_rewards = 10_000_000` lamports (deposited via `VoteInstruction::DepositDelegatorRewards`).
2. `V`'s total lamports = `rent_exempt_minimum + vat_to_burn_per_epoch + 1` — satisfies `minimum_vote_account_balance_for_vat()` and is admitted by `clone_and_filter_for_vat` (per [12](#0-11) ), since that check ignores `pending_delegator_rewards`.
3. At the epoch boundary, `maybe_burn_vat_from_staked_accounts` runs and subtracts `vat_to_burn_per_epoch` from `V` unconditionally (per [9](#0-8) ), leaving `V` with `rent_exempt_minimum + 1` lamports — far below `rent_exempt_minimum + pending_delegator_rewards`.
4. `V`'s authorized withdrawer can no longer withdraw even 1 lamport without violating the `pending_delegator_rewards` reserve check in `withdraw()` (per [7](#0-6) ) — but the 10,000,000 lamports the reserve was supposed to protect are already gone (burned to the incinerator), meaning the recorded `pending_delegator_rewards` liability is no longer backed by actual funds.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
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

**File:** programs/vote/src/vote_state/handler.rs (L190-209)
```rust
    pub(crate) fn pending_delegator_rewards(&self) -> u64 {
        match &self.target_state {
            TargetVoteState::V4(v4) => v4.pending_delegator_rewards,
        }
    }

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

**File:** runtime/src/bank.rs (L2648-2694)
```rust
    fn maybe_burn_vat_from_staked_accounts(&mut self, epoch_stakes: &VersionedEpochStakes) {
        let feature_snapshot = self.feature_set.snapshot();
        if !feature_snapshot.alpenglow {
            return;
        }

        let vat_to_burn_per_epoch = self.vat_to_burn_per_epoch();
        let vote_accounts = epoch_stakes.stakes().vote_accounts();
        debug_assert!(vote_accounts.len() <= 2000);
        // +1 for the incinerator account
        let mut accounts_to_store: Vec<(Pubkey, AccountSharedData)> =
            Vec::with_capacity(vote_accounts.len() + 1);
        let mut total_vat = 0u64;

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

        // Per SIMD-0357, transfer collected VAT to the incinerator account.
        let mut incinerator_account = self.get_account(&incinerator::id()).unwrap_or_default();
        incinerator_account.set_lamports(
            incinerator_account
                .lamports()
                .checked_add(total_vat)
                .unwrap(),
        );
        accounts_to_store.push((incinerator::id(), incinerator_account));

        self.store_accounts((self.slot, accounts_to_store.as_slice()), None);
        info!(
            "Transferred total VAT of {total_vat} lamports to incinerator from staked vote \
             accounts"
        );
    }
```

**File:** runtime/src/bank.rs (L6607-6620)
```rust
    /// Minimum balance a vote account must hold to survive SIMD-0357 filtering
    /// under the current feature set. When `alpenglow` is active the threshold
    /// also includes one epoch's worth of VAT burn.
    pub fn minimum_vote_account_balance_for_vat(&self) -> u64 {
        let vote_account_rent_exempt_minimum = self
            .rent_collector
            .rent
            .minimum_balance(VoteStateV4::size_of());
        if self.feature_set.snapshot().alpenglow {
            vote_account_rent_exempt_minimum + self.vat_to_burn_per_epoch()
        } else {
            vote_account_rent_exempt_minimum
        }
    }
```

**File:** vote/src/vote_account.rs (L212-232)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
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
        }
```
