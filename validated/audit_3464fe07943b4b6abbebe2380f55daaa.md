Audit Report

## Title
Vote account VAT burn can panic the validator because vote-account balance is not re-validated between admission filtering and the burn - (File: `runtime/src/bank.rs`)

## Summary
`Bank::maybe_burn_vat_from_staked_accounts` subtracts a fixed `vat_to_burn_per_epoch` from each vote account's lamport balance using `checked_sub(...).expect(...)`, relying entirely on the precondition that `Stakes::clone_and_filter_for_vat` already filtered vote accounts to have "enough balance for admission" [1](#0-0) . Because vote account balances can be reduced at any time via the permissionless vote-program `withdraw` instruction, which has no awareness of the pending VAT amount [2](#0-1) , the invariant asserted at filter-time can be violated by burn-time, triggering the `.expect()` panic deterministically across all validators processing that epoch boundary.

## Finding Description
The function iterates `epoch_stakes.stakes().vote_accounts().delegated_stakes()` and unconditionally computes `account.lamports().checked_sub(vat_to_burn_per_epoch).expect(...)` [3](#0-2) . Its doc comment explicitly states this must only be called after `clone_and_filter_for_vat` has filtered accounts to have "enough balance for admission" [4](#0-3) . `Stakes::clone_and_filter_for_vat` performs that filtering once, producing a new filtered `Stakes` snapshot with a `minimum_vote_account_balance` parameter [5](#0-4) .

Between the time this filtered epoch-stakes snapshot is computed and the time `maybe_burn_vat_from_staked_accounts` actually executes (fetching the *current* account state via `self.get_account(vote_pubkey)`), the vote account's real lamport balance can change. The vote program's `withdraw` instruction is fully permissioned only by the account's own authorized withdrawer (an ordinary, unprivileged signer) and enforces only that the remaining balance is zero or at least `rent_exempt_minimum + pending_delegator_rewards` — it has no concept of, and does not reserve, the `vat_to_burn_per_epoch` amount [6](#0-5) . Thus a withdrawer can legally reduce a vote account's balance below `vat_to_burn_per_epoch` after admission filtering but before the burn executes, causing `checked_sub` to return `None` and the `.expect()` to panic.

I was unable to fully trace the exact call-site ordering of `clone_and_filter_for_vat` versus `maybe_burn_vat_from_staked_accounts` (i.e., whether they operate on the same in-memory snapshot within a single atomic epoch-boundary step, or whether there is a genuine time window across slots during which a `Withdraw` transaction could land) within the available indexed code. This is the key uncertainty for confirming exploitability, and would require deeper tracing of epoch-boundary bank processing (e.g., `new_from_parent`/epoch-stakes computation call sites) than the current index exposed.

## Impact Explanation
If reachable, this causes a deterministic panic inside core, non-optional bank processing that every validator executes identically at the epoch boundary, which would constitute a network-wide consensus/liveness halt rather than a single-node crash — matching the "cause consensus halt" impact bucket. The severity is contingent on confirming the exploit window described above.

## Likelihood Explanation
The attacker capability required — issuing an ordinary `Withdraw` instruction as the vote account's authorized withdrawer — is unprivileged and requires no malicious peer or trusted role, satisfying baseline eligibility. However, likelihood hinges on the unconfirmed timing window between when the admission-filtered account list is computed and when the burn is applied against live account state; without confirming this window is exploitable (as opposed to being computed and consumed atomically from the same snapshot in a way that cannot be raced), the practical likelihood cannot be established with confidence from the available code.

## Recommendation
Regardless of the exact timing window, robustness should not depend on cross-boundary invariants enforced only by an earlier filter step. In `maybe_burn_vat_from_staked_accounts`, replace `checked_sub(...).expect(...)` with a saturating/checked computation that burns `min(current_balance - rent_exempt_minimum, vat_to_burn_per_epoch)` or otherwise gracefully skips/deactivates accounts that no longer qualify, rather than panicking on an unmet assumption.

## Proof of Concept
Confirming this requires: (1) tracing the exact call sites and timing relationship between `Stakes::clone_and_filter_for_vat` and `Bank::maybe_burn_vat_from_staked_accounts` in the epoch-boundary bank-processing pipeline, and (2) constructing a test (e.g., extending `runtime/src/bank/tests.rs` VAT-related tests) that creates a vote account balance near the admission threshold, issues a `Withdraw` transaction to reduce it below `vat_to_burn_per_epoch`, and then advances the bank to the epoch boundary to check whether the `.expect()` panic is reachable. This step could not be completed with the tools available in this pass.

### Citations

**File:** runtime/src/bank.rs (L2644-2676)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1062-1120)
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
```

**File:** runtime/src/stakes.rs (L247-257)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> Stakes<T> {
        Self::new(
            self.vote_accounts
                .clone_and_filter_for_vat(max_vote_accounts, minimum_vote_account_balance),
            self.epoch,
        )
    }
```
