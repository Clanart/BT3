## Analog Found: Filter/burn threshold mismatch can panic the validator during epoch-boundary VAT processing

### Title
`maybe_burn_vat_from_staked_accounts()` underflow panic when VAT burn amount changes between filtering and burning - (File: `runtime/src/bank.rs`)

### Summary
The Frankencoin bug is a class of "value computed with one rounding/threshold is later checked against a different (stricter) amount, causing legitimate operations to be rejected/reverted." The closest Agave analog is in the SIMD-0357/Alpenglow Validator Admission Ticket (VAT) machinery: vote accounts are filtered for "sufficient balance" using one snapshot of `vat_to_burn_per_epoch()`, but the balance is later debited using whatever `vat_to_burn_per_epoch()` currently evaluates to on the bank performing the burn, via an `expect()`-guarded `checked_sub` that panics on any mismatch instead of returning an error.

### Finding Description
`Bank::minimum_vote_account_balance_for_vat()` computes the filter threshold as `rent_exempt_minimum + self.vat_to_burn_per_epoch()`. [1](#0-0) 

`vat_to_burn_per_epoch()` is not a constant — it is looked up from `self.current_slot_params()`, which resolves to different values depending on which slot-time-reduction feature is active at `self.slot`. [2](#0-1) [3](#0-2) 

Vote accounts are filtered with this threshold in `compute_new_epoch_caches_and_rewards`: [4](#0-3) 

Later, at the epoch boundary, `update_epoch_stakes` calls `maybe_burn_vat_from_staked_accounts`, which re-derives `vat_to_burn_per_epoch()` from `self` (the bank at that point) and subtracts it unconditionally from every vote account's balance using `checked_sub(...).expect(...)`: [5](#0-4) 

The `.expect()` message explicitly assumes the earlier filtering step already guaranteed sufficient balance:
```
.expect(
    "Vote accounts should have already been filtered to contain enough \
     balance for the VAT",
)
``` [6](#0-5) 

This is structurally identical to the Frankencoin issue: a guard (`checkCollateral`/here, `checked_sub().expect()`) assumes an earlier-computed value (`price`/here, the filter threshold) stays consistent with a later strict comparison (`collateralReserve*atPrice`/here, the actual subtracted amount). If the two computations of `vat_to_burn_per_epoch()` diverge — because the bank instance used for filtering resolves to a different `SlotParams` than the bank instance used for burning (e.g., a slot-time-reduction feature's `feature_effective_slot` boundary falls between the two calls, or `update_epoch_stakes` uses the `get_top_epoch_stakes()` fallback path instead of the pre-filtered set noted in the code comment: "Other callers (same-epoch refresh, warps) fall back to `get_top_epoch_stakes`") — an account admitted by the filter with a balance in `[minimum_vote_account_balance_for_vat_OLD, minimum_vote_account_balance_for_vat_NEW)` will fail `checked_sub` and hit the `.expect()` panic. [7](#0-6) 

### Impact Explanation
`maybe_burn_vat_from_staked_accounts` runs unconditionally for every bank crossing an epoch boundary once `alpenglow` is active, as part of core bank/replay processing — not RPC or any external-input path. A panic here crashes the validator process on that code path. Because this executes deterministically as part of normal epoch-transition consensus processing (every validator processes the same epoch boundary), a systematic trigger (e.g. any legitimate slot-time-reduction feature activation landing at the wrong boundary relative to the fallback `get_top_epoch_stakes()` re-filter path) would cause correlated validator crashes across the network — a consensus-halt-class impact, not merely a single-node bug.

### Likelihood Explanation
Likelihood is speculative rather than confirmed: exploiting this requires the `update_epoch_stakes` fallback path (`get_top_epoch_stakes()`, used for "same-epoch refresh, warps") to re-run vote-account filtering with a `vat_to_burn_per_epoch()` value that differs from the one used by the original `compute_new_epoch_caches_and_rewards` filtering pass for the same epoch transition, which the in-repo comments suggest is intended to be consistent but is not enforced by any assertion at filter time — only assumed at burn time via the `.expect()` panic message. I could not fully trace every call path invoking `update_epoch_stakes` with the fallback branch within available iterations, so whether a slot-time-reduction feature activation can actually land inside the vulnerable window in practice is not fully verified from local code alone.

### Recommendation
Replace the `.expect()` panic in `maybe_burn_vat_from_staked_accounts` with `saturating_sub` (clamping burn to available balance) or, better, re-derive/assert the filter threshold and the burn amount from the exact same `SlotParams` snapshot at the point of filtering, and propagate a recoverable error instead of panicking if a mismatch is ever detected — mirroring the Frankencoin remediation of aligning the rounding/threshold used in the guard with the rounding/threshold used in the value it guards.

### Proof of Concept
No concrete PoC could be constructed from local code alone within the available search — the analysis is based on tracing the threshold-computation (`minimum_vote_account_balance_for_vat`) and the burn-guard (`checked_sub(...).expect(...)`) call sites and slot-parameter transition logic (`SlotParamsArchive::params_at_slot`) shown above; a full PoC would require constructing a bank sequence where `update_epoch_stakes` takes the `get_top_epoch_stakes()` fallback branch across a slot-time-reduction feature's `feature_effective_slot` boundary, which existing tests (`test_vat_burn_slot_params`) exercise only for the direct/non-fallback path. [8](#0-7)

### Citations

**File:** runtime/src/bank.rs (L1781-1787)
```rust
        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
```

**File:** runtime/src/bank.rs (L2608-2616)
```rust
            // At the epoch boundary, `compute_new_epoch_caches_and_rewards`
            // has already produced the VAT-filtered vote-account snapshot;
            // reuse it here instead of re-cloning and re-filtering the
            // `stakes_cache`. Other callers (same-epoch refresh, warps)
            // fall back to `get_top_epoch_stakes`.
            let stakes = match prefiltered_distribution_vote_accounts {
                Some(prefiltered) => Stakes::new(prefiltered, self.epoch()),
                None => self.get_top_epoch_stakes(),
            };
```

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

**File:** runtime/src/bank.rs (L2832-2835)
```rust
    /// Returns the Validator Admission Ticket burn for this bank's slot params.
    pub(crate) fn vat_to_burn_per_epoch(&self) -> u64 {
        self.current_slot_params().vat_to_burn_per_epoch()
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

**File:** runtime/src/slot_params.rs (L279-286)
```rust
    /// Returns the slot params effective at `slot`.
    pub(crate) fn params_at_slot(&self, slot: Slot) -> SlotParams {
        self.param_transitions
            .range(..=slot)
            .next_back()
            .map(|(_, params)| *params)
            .unwrap_or(LEGACY_SLOT_PARAMS)
    }
```

**File:** runtime/src/bank/tests.rs (L6863-6913)
```rust
#[test]
fn test_vat_burn_slot_params() {
    let voting_keypair = ValidatorVoteKeypairs::new_rand();
    let validator_keypairs = [&voting_keypair];
    let vote_pubkey = voting_keypair.vote_keypair.pubkey();

    // Loop through slot reduction features one at a time.
    for (slot_time_feature_id, params) in std::iter::once((None, LEGACY_SLOT_PARAMS))
        .chain(slot_time_feature_gates().map(|(feature_id, params)| (Some(feature_id), params)))
    {
        // Genesis with slot reduction feature active (if applicable).
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_vote_accounts_and_cluster_type(
            1_000 * LAMPORTS_PER_SOL,
            &validator_keypairs,
            vec![minimum_vote_account_balance_for_vat(100)],
            ClusterType::Development,
            &FeatureSet::default(),
            false,
        );
        activate_feature(&mut genesis_config, feature_set::alpenglow::id());
        if let Some(feature_id) = slot_time_feature_id {
            activate_feature(&mut genesis_config, feature_id);
        }

        // Advance forward such that feature goes effective.
        let (parent_bank, _bank_forks) = Bank::new_with_bank_forks_for_tests(&genesis_config);
        let bank_slot = if slot_time_feature_id.is_some() {
            parent_bank.epoch_schedule().get_first_slot_in_epoch(1)
        } else {
            parent_bank.slot().saturating_add(1)
        };
        let mut bank = Bank::new_from_parent(parent_bank, SlotLeader::default(), bank_slot);
        assert_eq!(bank.vat_to_burn_per_epoch(), params.vat_to_burn_per_epoch());

        // Verify correct VAT amount is burned.
        let vote_lamports_before = bank.get_balance(&vote_pubkey);
        let incinerator_lamports_before = bank.get_balance(&incinerator::id());
        let stakes = SerdeStakesToStakeFormat::from(bank.get_top_epoch_stakes());
        let epoch_stakes = VersionedEpochStakes::new(stakes, bank.epoch());
        bank.maybe_burn_vat_from_staked_accounts(&epoch_stakes);
        assert_eq!(
            bank.get_balance(&vote_pubkey),
            vote_lamports_before - params.vat_to_burn_per_epoch()
        );
        assert_eq!(
            bank.get_balance(&incinerator::id()),
            incinerator_lamports_before + params.vat_to_burn_per_epoch()
        );
    }
```
