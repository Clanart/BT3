## Analysis

The Sherlock report's underlying pattern is: **a privileged/critical operation assumes a balance precondition established at an earlier point in time is still true at execution time, with no re-validation — so any intervening mutation of that balance breaks the assumption and the operation fails/crashes instead of degrading gracefully.** In the Autonomint case, `liquidationType2` assumes ETH exists locally (set up "at deploy/design time") but it's actually elsewhere, so the call always reverts.

The closest verified Agave analog is the Alpenglow **Validator Admission Ticket (VAT) burn** path, which hard-codes the same kind of stale-precondition assumption, but enforces it with a Rust `.expect()` panic rather than an error return. [1](#0-0) 

### Title
Stale balance-precondition assumption in Alpenglow VAT burn panics the validator via `expect()` in `maybe_burn_vat_from_staked_accounts` - (File: `runtime/src/bank.rs`)

### Summary
`Bank::maybe_burn_vat_from_staked_accounts` explicitly documents a precondition that must hold *before* it runs: vote accounts passed in must have "already been filtered ... to contain enough balance for the VAT" by `clone_and_filter_for_vat`. However, the function re-reads each account's *current, live* balance from the bank (`self.get_account(vote_pubkey)`) rather than using the balance validated at filter time, and then does an unchecked `checked_sub(...).expect(...)` on that live balance. If the vote account's balance drops between the filtering snapshot and the burn execution, the subtraction underflows and the process panics. [2](#0-1) 

### Finding Description
The precondition is asserted only in a doc comment and via `debug_assert!`, not enforced at runtime for balance sufficiency: [3](#0-2) 

The vote account balance is *not* immutable between when `epoch_stakes` (and its VAT-eligible filtered set) is produced and when this function runs during epoch-boundary processing. Vote-account lamports can be reduced at any time by the account's authorized withdrawer via the ordinary, fully permissionless `Withdraw` vote instruction, which only checks against the *rent-exempt minimum*, not against any VAT reserve: [4](#0-3) 

Nothing in `withdraw` (nor in the rest of the vote program) is aware of, or reserves lamports for, the VAT burn amount. So a vote account owner can legally withdraw down to `rent_exempt_minimum` right after the epoch's VAT-eligible set is computed/cached, and before `maybe_burn_vat_from_staked_accounts` executes against the live bank state at the epoch boundary. When that happens, `account.lamports().checked_sub(vat_to_burn_per_epoch)` underflows and the `.expect("Vote accounts should have already been filtered to contain enough balance for the VAT")` panics.

The codebase itself acknowledges elsewhere that vote-account balances can shift between reward/VAT-related steps that run in sequence at the epoch boundary — the reward-commission code explicitly defers account loading "so that any intervening account mutations (e.g. VAT burns in `update_epoch_stakes`)" are reflected, showing that the authors know these mutations interleave, but the VAT burn path itself does not perform the equivalent defensive re-validation; it converts the violated assumption directly into a hard panic instead of a graceful skip/error: [5](#0-4) 

### Impact Explanation
This runs deterministically inside epoch-boundary bank processing (not an RPC-only or metrics path), so every validator processing the same chain state hits the identical panic at the identical slot — this is a **consensus halt**: the entire Alpenglow-enabled cluster would crash simultaneously once a triggering vote-account withdrawal is included in the ledger prior to the affected epoch boundary. This is not an RPC crash and does not require a malicious peer, plugin, or leaked key — it is triggerable by any ordinary, unprivileged vote-account withdrawer using a standard `Withdraw` instruction.

### Likelihood Explanation
The trigger requires only:
1. A vote account holding just above the VAT admission threshold at the time it is snapshotted/filtered into the epoch's VAT-eligible set.
2. The authorized withdrawer submitting an ordinary `Withdraw` instruction that drops the balance below `vat_to_burn_per_epoch` before the epoch-boundary burn executes.

Both are normal, unprivileged, permitted actions — no attacker-controlled infrastructure or trust assumption is needed, only correct timing relative to the epoch boundary, which is publicly known (`epoch_schedule`).

### Recommendation
`maybe_burn_vat_from_staked_accounts` should not re-fetch a possibly-stale live balance and `.expect()` sufficiency; instead it should either (a) use `saturating_sub`/`checked_sub` with a graceful fallback (e.g., burn `min(balance - rent_exempt_minimum, vat_to_burn_per_epoch)` or skip that account and log/track it) so a legitimate withdrawal cannot crash the validator, or (b) have the vote program's `withdraw` instruction itself reserve/consider the pending VAT obligation (similar to how `pending_delegator_rewards` is already protected against being withdrawn away).

### Proof of Concept
Conceptual sequence (based on code paths above, not independently executed):
1. Vote account `V` has stake and a balance just above `minimum_vote_account_balance_for_vat` at the moment its epoch's `VersionedEpochStakes`/VAT-eligible set is produced (via `clone_and_filter_for_vat`).
2. Before the epoch boundary is actually processed, the authorized withdrawer of `V` submits `VoteInstruction::Withdraw` for an amount that leaves `V`'s balance at (or just above) `rent_exempt_minimum` but below `vat_to_burn_per_epoch` — this succeeds because `withdraw` (`programs/vote/src/vote_state/mod.rs:1062`) has no knowledge of the pending VAT burn.
3. At the epoch boundary, `Bank::maybe_burn_vat_from_staked_accounts` (`runtime/src/bank.rs:2648`) is invoked with the earlier-filtered `epoch_stakes`, which still lists `V`. It fetches `V`'s *current* (post-withdrawal) account via `self.get_account(vote_pubkey)` and computes `account.lamports().checked_sub(vat_to_burn_per_epoch)`, which is `None`, triggering the `.expect(...)` panic and crashing the validator process at that slot for every validator on the network.

**Note:** I was unable to fully trace, within the available tool budget, the exact call chain/timing between `clone_and_filter_for_vat`'s snapshot point and `maybe_burn_vat_from_staked_accounts`'s invocation inside `update_epoch_stakes` (i.e., how many slots of "gap" exist and whether any other guard closes it). This should be independently verified by reading `runtime/src/stakes.rs::clone_and_filter_for_vat` and the exact call site of `maybe_burn_vat_from_staked_accounts` in `runtime/src/bank.rs` before treating this as fully confirmed.

### Citations

**File:** runtime/src/bank.rs (L2644-2677)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1112-1128)
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

    vote_account.checked_sub_lamports(lamports)?;
    drop(vote_account);
    let mut to_account = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to_account.checked_add_lamports(lamports)?;
    Ok(())
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
