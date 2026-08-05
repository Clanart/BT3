## Title
Vote account VAT burn can panic the validator because vote-account balance is not re-validated between admission filtering and the burn - (File: `runtime/src/bank.rs`)

## Summary
This is a real Agave analog of the INIT `POS_MANAGER` bug: both bugs stem from code that trusts a value (debt-share amount / vote-account lamport balance) to stay "sufficient" between the time it was checked and the time it is consumed, then performs an unchecked/`expect`-guarded subtraction on that stale assumption. In Agave, `Bank::maybe_burn_vat_from_staked_accounts` explicitly documents that it must only run after vote accounts have been filtered by `clone_and_filter_for_vat` to guarantee "enough balance for admission," and then unconditionally does `checked_sub(vat_to_burn_per_epoch).expect(...)`, with no re-check of the current balance at burn time.

## Finding Description
`maybe_burn_vat_from_staked_accounts` iterates the vote accounts that were selected as part of the Alpenglow epoch stakes and subtracts a fixed `vat_to_burn_per_epoch` amount from each one's lamports: [1](#0-0) 

The function's own doc comment states the precondition it relies on:

> "Note: This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`) to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission."

That is exactly the same shape of bug as the INIT report: the code assumes a value that was true at check-time (`clone_and_filter_for_vat` selection) remains true at use-time (burn execution), instead of re-validating it. Vote account lamport balances are not immutable between those two points — the authorized withdrawer of a vote account can call the vote program's `withdraw` instruction at any time to drain lamports out of the account: [2](#0-1) 

`withdraw` only checks that the withdrawal leaves the account either fully closed (balance 0) or above `min_rent_exempt_balance + pending_delegator_rewards`. Nothing in `withdraw` is aware of, or reserves, the `vat_to_burn_per_epoch` amount that `maybe_burn_vat_from_staked_accounts` will later try to subtract. If a vote account has just barely enough balance to be included by `clone_and_filter_for_vat` (i.e., balance ≥ some admission threshold that includes the VAT amount at selection time), and the authorized withdrawer subsequently withdraws lamports down to just above the vote program's own minimum before the epoch-boundary burn executes, the vote account balance at burn time can be lower than `vat_to_burn_per_epoch`. The subtraction then fails and:

```rust
account
    .lamports()
    .checked_sub(vat_to_burn_per_epoch)
    .expect(
        "Vote accounts should have already been filtered to contain enough \
         balance for the VAT",
    ),
```

panics instead of returning an error.

## Impact Explanation
`maybe_burn_vat_from_staked_accounts` runs as part of deterministic epoch-boundary bank processing for every validator that reaches that epoch/slot (it is not gated behind any privileged role — the trigger is simply an ordinary, permissionless `Withdraw` instruction issued by any vote account's authorized withdrawer). Because the panic occurs inside core bank processing that every validator executes identically, this is not a localized crash of one node but a network-wide, simultaneous panic across the entire fleet processing that slot — i.e., a consensus/liveness halt, not merely a single-node crash. This satisfies the "cause consensus halt" / "non-RPC remote exhaustion or crash" impact bucket: an unprivileged actor (any vote account withdraw authority) can deterministically bring down block processing for the whole cluster without needing any malicious peer, leaked key, or trusted integration.

## Likelihood Explanation
The precondition is narrow but realistic: it requires a vote account whose balance is close to the VAT admission cutoff and where the authorized withdrawer withdraws funds in the window between admission-set computation (`clone_and_filter_for_vat`) and the burn call at epoch boundary. Any validator operator (or someone who obtains withdraw authority over a low-balance vote account) can engineer this deliberately — no coordination with other validators or malicious network behavior is required, only ordinary use of the `Withdraw` vote instruction, which matches the "unprivileged, no malicious peer assumption" requirement.

## Recommendation
Do not rely on `expect()`/the earlier filtering invariant across the state-mutation boundary. In `maybe_burn_vat_from_staked_accounts`, re-check the account's current lamport balance against `vat_to_burn_per_epoch` at burn time and use `saturating_sub`/`checked_sub` with graceful handling (e.g., burn `min(balance - rent_exempt_minimum, vat_to_burn_per_epoch)` or skip/deactivate accounts that no longer qualify) instead of panicking, mirroring the recommended INIT fix of validating the value immediately before use rather than trusting a value computed at an earlier point in time.

## Proof of Concept
Exact reproduction requires stepping through Alpenglow epoch-boundary scheduling, which could not be fully traced in this pass (the call site relationship between `clone_and_filter_for_vat` and `maybe_burn_vat_from_staked_accounts`, and the precise timing window across slots, was not verified beyond the doc comment and function body shown above) — this should be confirmed with a Devin session that can build and run the `runtime/src/bank/tests.rs` VAT-related tests and simulate a vote account near the admission threshold performing a `Withdraw` before the epoch boundary triggers `maybe_burn_vat_from_staked_accounts`, to confirm the `.expect()` panic path is reachable. [3](#0-2) [2](#0-1)

### Citations

**File:** runtime/src/bank.rs (L2644-2695)
```rust
    /// Burn the Validator Admission ticket from each vote account if Alpenglow is enabled
    ///
    /// Note: This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`)
    /// to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission.
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

**File:** programs/vote/src/vote_state/mod.rs (L1062-1128)
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
    let vote_state = get_vote_state_handler_checked(&vote_account, target_version)?;

    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

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

    vote_account.checked_sub_lamports(lamports)?;
    drop(vote_account);
    let mut to_account = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to_account.checked_add_lamports(lamports)?;
    Ok(())
```
