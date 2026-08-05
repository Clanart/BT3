### Title
Vote-account `Withdraw` between VAT filtering and VAT burn can panic the bank, halting the cluster — (File: `runtime/src/bank.rs`)

### Summary
`Bank::maybe_burn_vat_from_staked_accounts` subtracts the epoch's Validator Admission Ticket (VAT) burn from every vote account's lamports using `checked_sub(..).expect(...)`, relying on the invariant that `clone_and_filter_for_vat` already guaranteed each account holds enough balance. That guarantee is only checked at the moment the epoch-stakes snapshot is filtered; nothing prevents a vote account's authorized withdrawer from legitimately withdrawing lamports afterward, before the burn is actually executed. This is the same broken-invariant class as the Symmetrical report: a value is checked to be "safe" at one point in time using an unsigned/gross calculation, but a legitimate, unprivileged action changes the underlying balance before the unsigned subtraction executes, causing an unhandled arithmetic failure instead of a graceful error.

### Finding Description
`maybe_burn_vat_from_staked_accounts` burns `vat_to_burn_per_epoch()` lamports from every vote account in `epoch_stakes.stakes().vote_accounts()`: [1](#0-0) 

The function's own doc comment states the precondition explicitly: [2](#0-1) 

The filtering that is supposed to guarantee "enough balance" is `VoteAccounts::clone_and_filter_for_vat`, which only compares each vote account's lamports at filter time against `minimum_vote_account_balance`: [3](#0-2) 

However, `epoch_stakes` (and the `VoteAccounts` embedded in it) is a snapshot computed once, while the actual burn happens later against the *live*, current account state fetched via `self.get_account(vote_pubkey)`: [4](#0-3) 

Between the snapshot and the burn, the vote account's authorized withdrawer can call the vote program's `Withdraw` instruction, which is a completely unprivileged, legitimate action gated only by the rent-exempt minimum and `pending_delegator_rewards` — it has no knowledge of, or enforcement for, the VAT threshold: [5](#0-4) 

Because `minimum_vote_account_balance_for_vat` (the threshold used at filter time) is strictly larger than the plain rent-exempt minimum enforced by `withdraw` (it adds `vat_to_burn_per_epoch()` on top of rent-exemption): [6](#0-5) 

a withdrawer can drain a vote account down to exactly the rent-exempt minimum after that account already passed VAT filtering. When the bank later executes `maybe_burn_vat_from_staked_accounts` for the corresponding epoch, `account.lamports().checked_sub(vat_to_burn_per_epoch).expect(...)` receives `None` and the `.expect()` panics.

### Impact Explanation
This code path executes deterministically as part of bank/epoch-boundary processing for every validator that replays the same block (it is not gated behind any RPC or plugin). A panic here brings down the validator process on every node that processes the offending slot, which is a cluster-wide consensus halt — the code comment even flags the very invariant that is violated ("Vote accounts should have already been filtered to contain enough balance for the VAT"), showing the developers assumed but did not enforce temporal consistency between the filter and the burn. No malicious peer, validator, or trusted component is required — it is triggered by a normal vote-account withdraw authority exercising a completely permitted instruction.

### Likelihood Explanation
The trigger requires only: (1) Alpenglow/VAT feature active, (2) a vote account that is at/near the VAT-inclusive minimum balance when the epoch-stakes snapshot used for VAT filtering is taken, and (3) its withdraw authority submitting an ordinary `Withdraw` instruction for the "extra" VAT-reserved lamports before the burn executes for that epoch. None of these require a compromised validator, elevated privileges, or unusual timing beyond normal transaction submission — any vote-account owner who is unaware of (or indifferent to) the VAT reservation can trigger it, intentionally or not.

### Recommendation
- Re-validate each vote account's balance against `vat_to_burn_per_epoch()` at burn time in `maybe_burn_vat_from_staked_accounts` instead of relying solely on the stale filter snapshot, and skip/adjust accounts that no longer qualify rather than panicking.
- Alternatively, enforce the VAT reserve directly in the vote program's `withdraw` instruction (similar to how `pending_delegator_rewards` is already reserved) so withdrawals cannot bring a vote account below the VAT-inclusive minimum while Alpenglow is active.
- Replace the `.expect()` panic with a saturating subtraction (skip burning, or burn only what's available and mark the account as failing VAT), so a stale invariant cannot escalate into a validator crash / consensus halt.

### Proof of Concept
Conceptual sequence (cannot be executed without a full local Agave test harness, but demonstrates the exact code path):
1. Enable the `alpenglow` feature; fund a vote account exactly at `minimum_vote_account_balance_for_vat()` (rent-exempt minimum + `vat_to_burn_per_epoch()`), matching `runtime/src/bank.rs:6607-6620`.
2. Let the epoch-stakes snapshot be computed while the account still holds this balance, so `clone_and_filter_for_vat` (`vote/src/vote_account.rs:212-232`) includes it as VAT-eligible.
3. Before the bank that will call `maybe_burn_vat_from_staked_accounts` processes the epoch boundary, submit a `VoteInstruction::Withdraw` for `vat_to_burn_per_epoch()` lamports via the account's withdraw authority — this succeeds because `vote_state::withdraw` (`programs/vote/src/vote_state/mod.rs:1112-1122`) only checks the rent-exempt minimum, leaving the account exactly rent-exempt.
4. When the bank subsequently executes `maybe_burn_vat_from_staked_accounts` (`runtime/src/bank.rs:2664-2677`) for that epoch's stakes, `checked_sub(vat_to_burn_per_epoch)` on the now-reduced balance returns `None`, and `.expect("Vote accounts should have already been filtered...")` panics, crashing the bank/validator process.

### Citations

**File:** runtime/src/bank.rs (L2644-2652)
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
```

**File:** runtime/src/bank.rs (L2662-2677)
```rust
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

**File:** programs/vote/src/vote_state/mod.rs (L1112-1122)
```rust
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
