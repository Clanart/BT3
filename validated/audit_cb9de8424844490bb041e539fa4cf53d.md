## Title
Genesis stake accounting reads a not-yet-initialized `feature_set`, causing incorrect initial delegated-stake/leader computation - (File: `runtime/src/bank.rs`)

### Summary
The Beanstalk report's broken invariant is: a per-asset config value (`stalkIssuedPerBdv`) is read from contract state during a migration/init step that runs *before* the step that actually populates that config value, silently corrupting an accounting computation with no revert. The Agave analog is the ordering of genesis bank construction in `Bank::new_from_genesis`: the stakes cache (which determines effective delegated stake and, from it, the initial leader and initial epoch stakes) is computed inside `process_genesis_config` using `self.feature_set`-derived parameters, but `self.feature_set` is only populated afterward by `compute_and_apply_genesis_features()`.

### Finding Description
In `Bank::new_from_genesis` (`runtime/src/bank.rs:1286-1357`), for a production (non `dev-context-only-utils`) build, the call order is: [1](#0-0) 
`process_genesis_config(genesis_config)` runs first, and `compute_and_apply_genesis_features()` (which is the routine that actually computes and assigns `self.feature_set = Arc::new(feature_set)` based on the accounts present in the genesis config) runs only afterward: [2](#0-1) 

However, inside `process_genesis_config`, the bank's `stakes_cache` is built immediately from the genesis accounts using two helpers that both read `self.feature_set`: [3](#0-2) 

Those helpers are: [4](#0-3) 

At the point `process_genesis_config` runs, `self.feature_set` is still whatever `Bank::default_with_accounts` produced — i.e. `FeatureSet::default()`, which marks every feature as **inactive**: [5](#0-4) 

So `new_warmup_cooldown_rate_epoch()` unconditionally returns `None` (as if `reduce_stake_warmup_cooldown` were inactive) and `use_fixed_point_stake_math()` unconditionally returns `false` (as if `upgrade_bpf_stake_program_to_v5_1` were inactive), **regardless of whether the genesis config actually activates those features from genesis**. This exactly parallels the Beanstalk bug: the reader (`stakes_cache` construction) fetches a feature-derived parameter from state before the writer (`compute_and_apply_genesis_features`) has run.

`Stakes::new_from_accounts_for_genesis` then consumes these wrong values to compute `delegation_effective_stake` for every genesis stake account and to build the initial `delegated_stakes`/`vote_accounts` map: [6](#0-5) 

That initial stakes snapshot is used immediately to pick the genesis leader (`highest_staked_node`) and is also copied into `epoch_stakes` for every epoch up to the leader-schedule epoch implied by slot 0: [7](#0-6) [8](#0-7) 

Nothing in this path reverts, asserts, or otherwise flags the discrepancy — exactly like the Beanstalk report, the corruption is silent and only manifests as wrong downstream numbers.

### Impact Explanation
If a warmup/cooldown or stake-math feature is intended to be active *from genesis* (a real, common pattern — see `genesis/src/main.rs` calling `activate_all_features`/`activate_all_features_alpenglow` before any bank exists), the genesis-time effective-stake computation silently uses the legacy (pre-feature) math instead of the intended one. This can change: (a) which validator is selected as the bootstrap/genesis leader (`self.leader = highest_staked_node`), and (b) the `VersionedEpochStakes` recorded for early epochs, which feed leader-schedule computation and vote-account weighting. An incorrect leader-schedule/stake baseline at genesis is a consensus-relevant miscalculation — every validator building the same genesis config would derive the *same* wrong result deterministically (this is not a fork risk since it's deterministic), but it means the cluster launches with a genesis-derived stake/leader state that does not match the intended feature-activated behavior, silently diverging from operator expectations and from any tooling that independently re-derives genesis stakes assuming the declared feature set is honored immediately.

### Likelihood Explanation
This triggers deterministically, with no attacker required, any time a genesis config activates `reduce_stake_warmup_cooldown` or `upgrade_bpf_stake_program_to_v5_1` (or any future feature gate whose effect is read through `new_warmup_cooldown_rate_epoch()`/`use_fixed_point_stake_math()`) at genesis and relies on `process_genesis_config`'s stakes-cache construction to honor it. Since `genesis/src/main.rs` explicitly calls `activate_all_features`/`activate_all_features_alpenglow` to set up genesis accounts for these features, any network genesis built this way is affected; likelihood of triggering the code path is high, though the practical divergence is limited to the exact genesis-epoch stake computation (warmup/cooldown rate and fixed-point math only matter for non-fully-activated delegations at slot 0, so the magnitude of impact depends on whether genesis stake delegations are marked as bootstrap/fully-active or use `activation_epoch = 0`, which I was unable to fully confirm from the available index — `stake_utils.rs`'s `create_stake_account`/`create_validator` content did not return matching lines in the final search pass).

### Recommendation
Reorder `Bank::new_from_genesis` so that `compute_and_apply_genesis_features()` (or at minimum the feature-set computation portion of it) runs *before* `process_genesis_config` builds `self.stakes_cache`, or pass the fully-computed `feature_set` explicitly into `process_genesis_config`/`Stakes::new_from_accounts_for_genesis` instead of reading `self.feature_set` implicitly. This mirrors the Beanstalk fix recommendation of passing the required config value explicitly rather than relying on ambient state that may not yet be initialized.

### Proof of Concept
Conceptual reproduction (cannot be executed in this environment, ask-only mode):
1. Build a `GenesisConfig` with `reduce_stake_warmup_cooldown` and `upgrade_bpf_stake_program_to_v5_1` marked active via `activate_all_features` (as `genesis/src/main.rs:840-844` does), and include at least one stake account whose `activation_epoch` is `0` (not the fully-activated bootstrap sentinel), so that warmup/cooldown math actually changes the effective stake at epoch 0.
2. Call `Bank::new_from_genesis(&genesis_config, ...)` in a production (non `dev-context-only-utils`) build.
3. Inspect the resulting `bank.leader` and `bank.epoch_stakes(0)` and compare against a manually computed `delegation_effective_stake` using the *intended* (feature-active) warmup/cooldown rate and fixed-point math.
4. Observe the mismatch: the bank's internally computed stake snapshot used the legacy math because `self.feature_set` was still `FeatureSet::default()` (all inactive) at the moment `process_genesis_config` built `stakes_cache`, confirming the ordering bug at `runtime/src/bank.rs:1328-1333` and `3203-3218`.

### Citations

**File:** runtime/src/bank.rs (L1328-1333)
```rust
        #[cfg(not(feature = "dev-context-only-utils"))]
        bank.process_genesis_config(genesis_config);
        #[cfg(feature = "dev-context-only-utils")]
        bank.process_genesis_config(genesis_config, leader_for_tests, genesis_hash);

        bank.compute_and_apply_genesis_features();
```

**File:** runtime/src/bank.rs (L1335-1345)
```rust
        // genesis needs stakes for all epochs up to the epoch implied by
        //  slot = 0 and genesis configuration
        {
            let stakes = bank.get_top_epoch_stakes();
            let stakes = SerdeStakesToStakeFormat::from(stakes);
            for epoch in 0..=bank.get_leader_schedule_epoch(bank.slot) {
                bank.epoch_stakes
                    .insert(epoch, VersionedEpochStakes::new(stakes.clone(), epoch));
            }
            bank.update_stake_history(None);
        }
```

**File:** runtime/src/bank.rs (L1711-1721)
```rust
    /// Epoch in which the new cooldown warmup rate for stake was activated
    pub fn new_warmup_cooldown_rate_epoch(&self) -> Option<Epoch> {
        self.feature_set
            .new_warmup_cooldown_rate_epoch(&self.epoch_schedule)
    }

    fn use_fixed_point_stake_math(&self) -> bool {
        self.feature_set
            .snapshot()
            .upgrade_bpf_stake_program_to_v5_1
    }
```

**File:** runtime/src/bank.rs (L3203-3218)
```rust
        self.stakes_cache = StakesCache::new(Stakes::new_from_accounts_for_genesis(
            self.new_warmup_cooldown_rate_epoch(),
            genesis_config.accounts.iter(),
            self.use_fixed_point_stake_math(),
        ));

        // After storing genesis accounts, the bank stakes cache will be warmed
        // up and can be used to set the leader id to the highest staked
        // node.
        let leader = self.stakes_cache.stakes().highest_staked_node();
        // If a leader is specified for test purposes, use that and if no leader found, use a random one.
        #[cfg(feature = "dev-context-only-utils")]
        let leader = leader_for_tests
            .or(leader)
            .or(Some(SlotLeader::new_unique()));
        self.leader = leader.expect("genesis processing failed because no staked nodes exist");
```

**File:** runtime/src/bank.rs (L6087-6107)
```rust
    /// Compute and apply all activated features and also add accounts for builtins
    fn compute_and_apply_genesis_features(&mut self) {
        // Update the feature set to include all features active at this slot
        let feature_set = self.compute_active_feature_set(false).0;
        self.feature_set = Arc::new(feature_set);

        // Apply rent deprecation feature if it's active at genesis
        // After feature cleanup, assert that rent exemption threshold is 1.0
        if self
            .feature_set
            .snapshot()
            .deprecate_rent_exemption_threshold
        {
            self.rent_collector.deprecate_rent_exemption_threshold();
        }

        // Add built-in program accounts to the bank if they don't already exist
        self.add_builtin_program_accounts();

        self.apply_activated_features();
    }
```

**File:** feature-set/src/lib.rs (L201-212)
```rust
impl Default for FeatureSet {
    fn default() -> Self {
        // All features disabled
        let active = AHashMap::new();
        let snapshot = FeatureSnapshot::from(&active);
        Self {
            active,
            inactive: AHashSet::from_iter((*FEATURE_NAMES).keys().cloned()),
            snapshot,
        }
    }
}
```

**File:** runtime/src/stakes.rs (L281-322)
```rust
impl Stakes<StakeAccount> {
    pub(crate) fn new_from_accounts_for_genesis<'a, T: ReadableAccount + 'a>(
        new_rate_activation_epoch: Option<Epoch>,
        accounts: impl IntoIterator<Item = (&'a Pubkey, &'a T)>,
        use_fixed_point_stake_math: bool,
    ) -> Self {
        let stake_history = StakeHistory::default();
        let mut vote_accounts = VoteAccountsHashMap::default();
        let mut delegated_stakes = DelegatedStakes::default();
        let mut stake_delegations = ImblHashMap::new();
        let epoch = 0;

        for (pubkey, account) in accounts {
            if account.lamports() == 0 {
                continue;
            }

            if solana_vote_program::check_id(account.owner()) {
                if VoteStateVersions::is_correct_size_and_initialized(account.data())
                    && let Ok(vote_account) =
                        VoteAccount::try_from(create_account_shared_data(account))
                {
                    vote_accounts.insert(*pubkey, (0, vote_account));
                }
            } else if stake_program::check_id(account.owner())
                && let Ok(stake_account) =
                    StakeAccount::try_from(create_account_shared_data(account))
            {
                let delegation = stake_account.delegation();
                let stake = delegation_effective_stake(
                    delegation,
                    epoch,
                    &stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if stake != 0 {
                    *delegated_stakes.entry(delegation.voter_pubkey).or_default() += stake;
                }
                stake_delegations.insert(*pubkey, stake_account);
            }
        }
```
