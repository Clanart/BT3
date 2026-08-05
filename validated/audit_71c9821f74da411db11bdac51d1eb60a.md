### Title
Panic-inducing unchecked lamport subtraction in Alpenglow VAT burn - (`runtime/src/bank.rs`)

### Summary
`Bank::maybe_burn_vat_from_staked_accounts` subtracts a fixed "Validator Admission Ticket" (VAT) burn amount from every eligible vote account's lamport balance using `checked_sub(...).expect(...)`, relying entirely on an out-of-band invariant (a prior filtering pass) rather than a runtime check. This mirrors the report's core bug class: an unchecked subtraction that is only "safe" because of an assumption that can, in principle, be violated, at which point the code panics instead of gracefully rejecting the case.

### Finding Description
`maybe_burn_vat_from_staked_accounts` iterates every vote account in the just-built `epoch_stakes` snapshot and does: [1](#0-0) 
```
for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
    let mut account = self.get_account(vote_pubkey).unwrap();
    total_vat += vat_to_burn_per_epoch;
    account.set_lamports(
        account.lamports().checked_sub(vat_to_burn_per_epoch).expect(
            "Vote accounts should have already been filtered to contain enough balance for the VAT",
        ),
    );
    ...
}
```
The comment above the function makes the invariant explicit: "This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`) to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission." [2](#0-1) 

The filtering that is supposed to guarantee sufficiency is `VoteAccounts::clone_and_filter_for_vat`, which checks `vote_account.lamports() >= minimum_vote_account_balance` on a *cached* `VoteAccount` view taken from `unfiltered_distribution_vote_accounts` (itself derived from `self.stakes_cache.stakes()` inside `compute_new_epoch_caches_and_rewards`): [3](#0-2) 

However, the value actually debited in `maybe_burn_vat_from_staked_accounts` is not read from that filtered snapshot's cached balance — it is re-fetched live from the accounts store via `self.get_account(vote_pubkey)`: [4](#0-3) . These are two independently maintained representations of "the vote account's lamports": one baked into the `Stakes`/`VoteAccounts` structure at filter time, and one read directly from the account store at burn time. The `.expect()` has no fallback — any divergence between the two, however it arises, converts what should be a rejected/degraded case into a hard panic, i.e., a `bank.rs` invariant violation that aborts the validator process rather than returning an `InstructionError` or similar recoverable error (contrast with the analogous, but safely-guarded, `vote_state::withdraw` path which returns `InstructionError::InsufficientFunds` instead of panicking: [5](#0-4) ).

### Impact Explanation
`process_new_epoch` (and therefore `maybe_burn_vat_from_staked_accounts`) runs unconditionally for every bank that crosses an epoch boundary, as part of `prepare_for_block_execution`, which every validator executes identically when replaying or producing the epoch-boundary block: [6](#0-5) . Because the corrupted/underflowing subtraction is deterministic given the same input state, if the divergence between the filter-time cached balance and the burn-time live balance is ever triggered, the panic fires on every validator processing that same block, producing a synchronized crash of the entire fleet at the epoch boundary — a non-RPC, remote-triggerable liveness/consensus-halt condition, not merely a local bug.

### Likelihood Explanation
I was not able to fully trace, within the available search budget, a concrete transaction sequence that forces `unfiltered_distribution_vote_accounts`'s cached lamports (used for filtering) to diverge from the value returned by `self.get_account()` at burn time within the same synchronous `process_new_epoch` call (no user transactions execute between the filter step and the burn step in the normal code path, so under ordinary conditions the two reads should agree). This means the likelihood cannot be confirmed as "certain" from local evidence alone — it depends on whether any code path can leave the `Stakes`/`VoteAccounts` cache's lamport value inconsistent with the accounts-db value for a vote account between the two reads (e.g., stale cache entries, warp/snapshot-restore paths, or a vote account being modified through the `AlpenglowEpochType::MigrationEpoch`/reward-commission distribution machinery that runs in `begin_partitioned_rewards` *after* the burn but whose ordering assumptions I could not fully verify). This is flagged as a genuine architectural smell (unchecked subtraction defended only by a comment/invariant, not a runtime guard) worth deeper investigation, but I cannot assert with full confidence that it is reachable without further tracing of `Stakes::check_and_store` and the stakes-cache update lifecycle across epoch/warp boundaries, which I did not have iterations remaining to complete.

### Recommendation
Replace the `.expect()` panic with a graceful, safe fallback: use `checked_sub` and, on `None`, skip burning from (or zero-cap the burn for) that specific vote account and log/metric the anomaly rather than aborting bank processing. Additionally, make the invariant self-verifying by re-deriving `minimum_vote_account_balance_for_vat` from the *same* live account read used for the burn (`self.get_account`), rather than trusting a separately-sourced cached snapshot from the filtering step, so filtering and burning always observe the same value.

### Proof of Concept
Not fully constructed — I could not confirm a concrete transaction/timing sequence that forces the cached filter-time lamports to disagree with the live burn-time lamports within the current search scope. A background engineer with full repo/test access should:
1. Trace `Stakes::check_and_store` and confirm whether any code path can update a vote account's on-chain lamports without updating the corresponding cached `VoteAccount` entry consumed by `calculate_activated_stake`/`clone_and_filter_for_vat` before the next epoch boundary.
2. If such a path exists (e.g., via snapshot restore, warp, or an out-of-band lamport mutation), construct a test bank that crosses an Alpenglow epoch boundary with a vote account whose cached balance is `>= minimum_vote_account_balance_for_vat` but whose live balance is `< vat_to_burn_per_epoch`, and confirm the `.expect()` panic fires in `maybe_burn_vat_from_staked_accounts`.

### Citations

**File:** runtime/src/bank.rs (L1816-1860)
```rust
    fn process_new_epoch(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_capitalization: u64,
        parent_height: u64,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
    ) {
        let epoch = self.epoch();
        let slot = self.slot();
        let thread_pool = rewards_calculation_thread_pool();

        let (_, apply_feature_activations_time_us) = measure_us!(
            thread_pool.install(|| { self.compute_and_apply_new_feature_activations() })
        );

        let mut rewards_metrics = RewardsMetrics::default();
        let NewEpochBundle {
            stake_history,
            unfiltered_distribution_vote_accounts,
            delegated_stakes,
            filtered_distribution_vote_accounts,
            rewards_calculation,
            calculate_activated_stake_time_us,
            update_rewards_with_thread_pool_time_us,
        } = self.compute_new_epoch_caches_and_rewards(
            thread_pool,
            parent_epoch,
            reward_calc_tracer,
            &mut rewards_metrics,
        );

        self.stakes_cache.activate_epoch(
            epoch,
            stake_history,
            unfiltered_distribution_vote_accounts,
            delegated_stakes,
        );

        // Save a snapshot of stakes for use in consensus and stake weighted networking
        let leader_schedule_epoch = self.epoch_schedule.get_leader_schedule_epoch(slot);
        let (_, update_epoch_stakes_time_us) = measure_us!(self.update_epoch_stakes(
            leader_schedule_epoch,
            Some(filtered_distribution_vote_accounts),
        ));
```

**File:** runtime/src/bank.rs (L2644-2663)
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
```

**File:** runtime/src/bank.rs (L2664-2676)
```rust
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

**File:** vote/src/vote_account.rs (L212-231)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1079-1082)
```rust
    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;
```
