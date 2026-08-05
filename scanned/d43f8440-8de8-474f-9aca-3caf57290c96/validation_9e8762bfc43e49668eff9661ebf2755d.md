## Title
Unprivileged dust pre-funding bypasses the SIMD-0232/0392 rent-exemption gate for commission/fee collector accounts - (`runtime/src/bank/fee_distribution.rs`)

### Summary
The Fei/Tribe report describes a "balance != 0" gate meant to signal "this account has been genuinely funded/initialized" that a malicious user can satisfy with a dust deposit, permanently unlocking downstream logic that should only run once real funding occurred. The Agave analog is `Bank::collector_type_checked`, which decides whether a custom commission/fee collector account is allowed to receive inflation/vote/fee deposits. Its rent-exemption requirement is silently waived whenever `pre_lamports != 0`, i.e. whenever the collector account had *any* nonzero balance the instant before the deposit. Any unprivileged actor can seed a target collector address with 1 lamport ahead of time via an ordinary system transfer (the recipient never needs to sign), permanently satisfying `pre_lamports != 0` for every subsequent call and disabling the rent-exemption check that the protocol otherwise enforces for that address.

### Finding Description
`collector_type_checked` is the guard used both for per-slot transaction-fee deposits (`deposit_fees`) and for epoch/commission reward payouts (`RewardCommissionAccounts`): [1](#0-0) 

The relevant branch is:
```rust
if !rent.is_exempt(account.lamports(), account.data().len())
    && (!relax_post_execution_balance_checks || pre_lamports == 0)
{
    Err(DepositFeeError::InvalidRentPayingAccount)
} else {
    Ok(ExternalCollectorType::SystemAccount)
}
```
When `relax_post_exec_min_balance_check` is active (documented as a SIMD-0392 grandfathering mechanism for legacy rent-paying accounts), the rent-exempt requirement is only enforced if `pre_lamports == 0`, i.e. if the account was previously empty/nonexistent. `pre_lamports` is read fresh from the account state on every call: [2](#0-1) 

and identically in the epoch-reward commission path: [3](#0-2) 

Nothing ties `pre_lamports != 0` to "this account was legitimately rent-exempt before the current rent parameters changed" (the actual SIMD-0392 intent). It is satisfied by *any* nonzero lamport balance, however it got there. Since any unprivileged account can transfer lamports to an arbitrary pubkey without the recipient's signature (`system_processor::transfer`/`transfer_verified` only requires the sender to sign — [4](#0-3) ), an attacker can pre-fund a soon-to-be-designated commission/block-revenue collector address with a single lamport before it is ever used as a collector. From that point on, `pre_lamports` will never again read as `0` for that address (deposits only add lamports), so the rent-exemption requirement in `collector_type_checked` is permanently disabled for that account — exactly mirroring the TribeRedeemer pattern of a dust pre-fund flipping a "not yet funded" gate to "already funded," bypassing a safety check meant to gate on genuine funding history.

### Impact Explanation
This breaks the invariant that non-vote-account commission collectors must be, and remain, rent-exempt (per SIMD-0232), a check whose entire purpose is to prevent reward/fee lamports from accumulating in an account that is not economically guaranteed to persist. Once bypassed by 1-lamport pre-funding, a collector address can keep receiving inflation-rewards commission, block-revenue commission, and priority-fee deposits indefinitely while remaining below the rent-exempt minimum, in direct violation of the protocol's stated invariant. This is a false-acceptance of a collector account that should have been rejected/burned per the enforced rules, and it can be triggered purely by an unprivileged third party front-running the account's designation with a trivial system transfer — no special privileges, malicious validator assumption, or trusted-process compromise required.

### Likelihood Explanation
Likelihood is high for the setup step: sending 1 lamport to a known/predictable future collector pubkey is a normal, cheap, unprivileged `Transfer` instruction. The precondition is simply that the attacker act before the target account first receives a commission/fee deposit — plausible because commission-collector pubkeys are often known or guessable ahead of a validator's `UpdateCommissionCollector`/`InitializeAccountV2` call (they must pass the same `NewCommissionCollector::validate_and_resolve_key` checks, so they are public transaction data once submitted, and can be front-run before the first actual deposit epoch).

### Recommendation
Do not use "current account balance is nonzero" as a proxy for "was already rent-exempt / already a legitimate grandfathered collector." Instead, gate the relaxation on the account's rent-exemption status prior to the current deposit (i.e., require `rent.is_exempt(pre_lamports, data_len)` rather than `pre_lamports == 0`) so that only accounts that were already fully rent-exempt are grandfathered, and any account that never reached rent-exemption continues to be rejected/burned regardless of dust pre-funding.

### Proof of Concept
1. Attacker determines/observes a pubkey `C` that a validator intends to designate as its `block_revenue_collector` or `inflation_rewards_collector` (via `UpdateCommissionCollector`/`InitializeAccountV2`, whose target key must pass `NewCommissionCollector::validate_and_resolve_key` — [5](#0-4) ).
2. Before `C` is ever used as a collector (and before it is rent-exempt), attacker submits an ordinary `SystemInstruction::Transfer` sending 1 lamport to `C`. This requires no signature from `C` ( [6](#0-5) ).
3. When the validator's commission is later paid via `deposit_fees`/`RewardCommissionAccounts`, `pre_lamports` for `C` is read as `1` (nonzero) instead of `0`.
4. `collector_type_checked` now takes the `relax_post_execution_balance_checks` branch and returns `Ok(ExternalCollectorType::SystemAccount)` even though `C` is not rent-exempt, permanently bypassing the intended rent-exemption enforcement for all future deposits to `C` ( [7](#0-6) ), which the existing test suite confirms is otherwise required to be `false, false, 1` (`UninitializedToSubRentExemptMinimum` case fails when `pre_balance == 0`) — [8](#0-7) .

### Citations

**File:** runtime/src/bank/fee_distribution.rs (L183-203)
```rust
    fn deposit_fees(&self, collector_id: &Pubkey, fees: u64) -> Result<u64, DepositFeeError> {
        let mut account = self
            .get_account_with_fixed_root_no_cache(collector_id)
            .unwrap_or_default();

        let feature_snapshot = self.feature_set.snapshot();
        if feature_snapshot.custom_commission_collector {
            let pre_lamports = account.lamports();
            account
                .checked_add_lamports(fees)
                .map_err(|_| DepositFeeError::LamportOverflow)?;
            if collector_id != &self.leader.vote_address {
                Bank::collector_type_checked(
                    collector_id,
                    pre_lamports,
                    &account,
                    &self.reserved_account_keys,
                    &self.rent_collector().rent,
                    feature_snapshot.relax_post_exec_min_balance_check,
                )?;
            }
```

**File:** runtime/src/bank/fee_distribution.rs (L241-270)
```rust
    pub(super) fn collector_type_checked(
        collector_id: &Pubkey,
        pre_lamports: u64,
        account: &AccountSharedData,
        reserved_account_keys: &ReservedAccountKeys,
        rent: &Rent,
        relax_post_execution_balance_checks: bool,
    ) -> Result<ExternalCollectorType, DepositFeeError> {
        if !system_program::check_id(account.owner()) {
            return Err(DepositFeeError::InvalidAccountOwner);
        }

        if reserved_account_keys.is_reserved(collector_id) {
            return Err(DepositFeeError::ReservedCollector);
        }

        // Don't perform rent check on the incinerator, so that the deposit
        // always works. The incinerator is run at the end of a block
        if *collector_id == incinerator::id() {
            Ok(ExternalCollectorType::Incinerator)
        } else {
            if !rent.is_exempt(account.lamports(), account.data().len())
                && (!relax_post_execution_balance_checks || pre_lamports == 0)
            {
                Err(DepositFeeError::InvalidRentPayingAccount)
            } else {
                Ok(ExternalCollectorType::SystemAccount)
            }
        }
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L600-608)
```rust
                let (pre_balance, deposit, should_succeed) = match collector_state {
                    CollectorState::InitializedToSubRentExemptMinimum => (
                        rent_exempt_minimum - 2,
                        1,
                        relax_post_exec_min_balance_check,
                    ),
                    CollectorState::UninitializedToSubRentExemptMinimum => (0, 1, false),
                    CollectorState::UninitializedToRentExempt => (0, rent_exempt_minimum, true),
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1128-1171)
```rust
                        let maybe_commission_account =
                            self.get_account_with_fixed_root_no_cache(commission_pubkey);
                        let mut commission_account = if custom_commission_collector {
                            // If the account doesn't exist, the vote commission
                            // may be enough lamports to cover rent-exemption
                            // and properly create the commission account.
                            maybe_commission_account.unwrap_or_default()
                        } else {
                            // Before SIMD-0232, commission accounts were always
                            // vote accounts, which cannot be closed unless the
                            // account hasn't voted for at least a full epoch.
                            // This means that `maybe_commission_account` should
                            // always exist.
                            let Some(commission_account) = maybe_commission_account else {
                                debug!(
                                    "commission account {commission_pubkey} missing at \
                                     distribution time"
                                );
                                return None;
                            };
                            commission_account
                        };
                        if *burned_lamports != 0 {
                            total_non_incinerator_burned_lamports
                                .fetch_add(*burned_lamports, Relaxed);
                        }
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
                        if !is_vote_account {
                            match Self::collector_type_checked(
                                commission_pubkey,
                                pre_lamports,
                                &commission_account,
                                reserved_account_keys,
                                rent,
                                relax_post_exec_min_balance_check,
                            ) {
```

**File:** programs/system/src/system_processor.rs (L216-243)
```rust
fn transfer_verified(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.get_data().is_empty() {
        ic_msg!(invoke_context, "Transfer: `from` must not carry data");
        return Err(InstructionError::InvalidArgument);
    }
    if lamports > from.get_lamports() {
        ic_msg!(
            invoke_context,
            "Transfer: insufficient lamports {}, need {}",
            from.get_lamports(),
            lamports
        );
        return Err(SystemError::ResultWithNegativeLamports.into());
    }

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;
    Ok(())
}
```

**File:** programs/system/src/system_processor.rs (L245-268)
```rust
fn transfer(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    if !instruction_context.is_instruction_account_signer(from_account_index)? {
        ic_msg!(
            invoke_context,
            "Transfer: `from` account {} must sign",
            instruction_context.get_key_of_instruction_account(from_account_index)?,
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    transfer_verified(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
}
```

**File:** programs/vote/src/vote_state/mod.rs (L875-904)
```rust
    pub fn validate_and_resolve_key(
        &self,
        vote_account: &BorrowedInstructionAccount,
        rent: &Rent,
    ) -> Result<Pubkey, InstructionError> {
        match self {
            NewCommissionCollector::VoteAccount => Ok(*vote_account.get_key()),
            NewCommissionCollector::NewAccount(collector_account) => {
                // 1. Must be a system program owned account.
                if collector_account.get_owner() != &system_program::id() {
                    return Err(InstructionError::InvalidAccountOwner);
                }

                // 2. Must be rent-exempt.
                if !rent.is_exempt(
                    collector_account.get_lamports(),
                    collector_account.get_data().len(),
                ) {
                    return Err(InstructionError::InsufficientFunds);
                }

                // 3. Must not be a reserved account (checked via writable flag).
                if !collector_account.is_writable() {
                    return Err(InstructionError::InvalidArgument);
                }

                Ok(*collector_account.get_key())
            }
        }
    }
```
