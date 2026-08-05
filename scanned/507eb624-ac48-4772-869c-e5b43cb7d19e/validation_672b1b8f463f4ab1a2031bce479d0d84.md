### Title
Inconsistent selection of `use_fixed_point_stake_math` across stake-cache mutation paths can desynchronize `delegated_stakes` from the true feature-activated math, corrupting vote-weight/stake accounting used for consensus - (File: `runtime/src/stakes.rs`, `runtime/src/stake_delegation.rs`)

### Summary
The Balancer report's root cause is that two call sites compute the *same* invariant with *different* rounding rules, so a value meant to be internally consistent (the invariant used for spot-price cross-checking) silently diverges depending on which code path computed it. Agave has a structurally identical pattern: stake "effective stake" math has two divergent implementations — the legacy floating/rational math (`Delegation::stake`/`stake_activating_and_deactivating`) and the new fixed-point math (`Delegation::stake_v2`/`stake_activating_and_deactivating_v2`) — selected per call via a boolean parameter `use_fixed_point_stake_math`, gated by the `upgrade_bpf_stake_program_to_v5_1` feature [1](#0-0) . This flag is threaded manually through many independent call sites in `StakesCache`/`Stakes` (`check_and_store`, `upsert_stake_delegation`, `remove_stake_delegation`, `calculate_delegated_stakes`, `calculate_activated_stake`, `refresh_delegated_stakes`) [2](#0-1) [3](#0-2) [4](#0-3) , as well as in reward/points calculation (`delegation_effective_stake`, `calculate_alpenglow_points`, `calculate_block_reward`) [5](#0-4) [6](#0-5) .

### Finding Description
Because the boolean is passed by value from each caller rather than derived from a single, centrally-enforced source of truth, any two call sites that recompute "effective stake" for the *same* account and *same* epoch will silently produce different results if one caller resolves the flag from a stale/parent `feature_set` snapshot while another resolves it from a different (child/current) snapshot, or if one caller simply forgets to flip it after the feature activates. This mirrors the Balancer bug exactly: the invariant/stake value is supposed to be a single canonical quantity but is instead computed via two math implementations that can disagree by design, and the discrepancy is silent (no assertion enforces that `stake()` and `stake_v2()` in the transitional epoch must agree, nor that the boolean passed to `upsert_stake_delegation` matches the one used later by `calculate_delegated_stakes`/`refresh_delegated_stakes` for the same object).

Concretely: `StakesCache::check_and_store` is invoked incrementally as accounts are updated during transaction processing, while `Stakes::calculate_activated_stake`/`refresh_delegated_stakes` are invoked once per epoch boundary — both call `delegation_effective_stake` with independently-computed `use_fixed_point_stake_math` [7](#0-6) [8](#0-7) . If the value fed to these two families of call sites is not derived identically (e.g., one uses `bank.feature_set.snapshot()` at a different point in bank lifecycle than another, or if the CLI/RPC path in `cli/src/stake.rs`/`cli/src/cluster_query.rs` derives it from a client-observed feature-activation epoch instead of the bank's authoritative feature set) [9](#0-8) , `delegated_stakes`/vote-account stake weights can diverge from the value the rest of consensus (leader schedule, vote weighting via `staked_nodes`) expects [10](#0-9) .

### Impact Explanation
`delegated_stakes` and `VoteAccounts` stake weights feed directly into leader-schedule derivation (`staked_nodes`) and reward distribution (`calculate_block_reward`, `calculate_alpenglow_points`). A silent divergence between the legacy and fixed-point stake math for the same delegation — if triggered by inconsistent flag propagation across the incremental (`check_and_store`) and epoch-boundary (`calculate_activated_stake`) code paths — would corrupt a value that must be bit-identical across all validators for consensus to hold, potentially causing incorrect reward payouts (fund loss) or a leader-schedule/vote-weight mismatch between nodes that computed the flag differently (consensus divergence).

### Likelihood Explanation
This is **low-to-uncertain likelihood** based on local evidence alone. I was not able to fully trace, within the remaining budget, whether every call site of `use_fixed_point_stake_math` is actually guaranteed (by a single shared `feature_snapshot()` per bank/slot) to always agree for a given epoch, or whether any code path (especially CLI/RPC estimators in `cli/src/stake.rs` and `cli/src/cluster_query.rs`, which independently poll `get_feature_activation_epoch`) could disagree with the bank's own resolution and thereby produce a wrong (but only locally-displayed) stake figure rather than a consensus-affecting one. The `runtime/src/bank.rs` call sites (9-12 matches) that would confirm or refute a genuine cross-node inconsistency were not fully read before the iteration budget was exhausted.

### Recommendation
Verify that every consensus-relevant call site of `delegation_effective_stake`/`delegation_activation_status`/`effective_stake` derives `use_fixed_point_stake_math` from exactly one authoritative source (`bank.feature_set.is_active(&upgrade_bpf_stake_program_to_v5_1::id())` evaluated at a single, well-defined point per epoch, e.g. via `feature_snapshot()`), and add a debug assertion or unit test that `stakes.rs` and `partitioned_epoch_rewards` resolve the same boolean for the same epoch/slot. CLI/RPC estimation paths in `cli/src/stake.rs`/`cli/src/cluster_query.rs` should be clearly documented as best-effort/non-authoritative so they are not mistaken for a consensus-critical computation.

### Proof of Concept
Not constructed — this requires confirming, via `runtime/src/bank.rs`, whether any two call sites resolving `use_fixed_point_stake_math` for the same bank/epoch could actually diverge (e.g., a snapshot taken before vs. after a feature activates mid-processing, or a warp/rewind path recomputing stakes with a different snapshot than the one used originally). This could not be verified within the available tool budget; a full trace of `runtime/src/bank.rs`'s 9 `use_fixed_point_stake_math` usages against `stakes.rs`'s usages is needed before this can be escalated from "structural analog" to a "confirmed exploitable divergence."

**Confidence caveat:** Given the incomplete trace, treat this as a plausible structural analog to the Balancer rounding-mismatch bug class (two divergent implementations of one "should-be-canonical" value, selected inconsistently across call sites) rather than a fully confirmed vulnerability. A Devin session with terminal/file access should complete the trace of `runtime/src/bank.rs` and confirm whether `use_fixed_point_stake_math` can ever be resolved inconsistently for the same bank/epoch across the incremental (`check_and_store`) vs. epoch-boundary (`calculate_activated_stake`) vs. reward (`calculate_stake_rewards_and_commissions`) code paths before this is reported upstream.

### Citations

**File:** runtime/src/stake_delegation.rs (L9-23)
```rust
#[inline]
pub(crate) fn delegation_effective_stake<T: StakeHistoryGetEntry>(
    delegation: &Delegation,
    epoch: Epoch,
    history: &T,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    if use_fixed_point_stake_math {
        delegation.stake_v2(epoch, history, new_rate_activation_epoch)
    } else {
        #[allow(deprecated)]
        delegation.stake(epoch, history, new_rate_activation_epoch)
    }
}
```

**File:** runtime/src/stakes.rs (L87-164)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
        let owner = account.owner();
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
                Err(_) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_stake_delegation(
                        pubkey,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
            }
        }
    }
```

**File:** runtime/src/stakes.rs (L259-265)
```rust
    pub fn vote_accounts(&self) -> &VoteAccounts {
        &self.vote_accounts
    }

    pub(crate) fn staked_nodes(&self) -> Arc<HashMap<Pubkey, u64>> {
        self.vote_accounts.staked_nodes()
    }
```

**File:** runtime/src/stakes.rs (L434-502)
```rust
    pub(crate) fn calculate_activated_stake(
        &self,
        next_epoch: Epoch,
        thread_pool: &ThreadPool,
        new_rate_activation_epoch: Option<Epoch>,
        stake_delegations: &[(&Pubkey, &StakeAccount)],
        use_fixed_point_stake_math: bool,
    ) -> (
        StakeHistory,
        VoteAccounts,
        DelegatedStakes,
        RewardEpochDelegatedStakes,
    ) {
        // Wrap up the prev epoch by adding new stake history entry for the
        // prev epoch.
        let (stake_history_entry, effective_delegated_stakes) = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .fold(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(acc, mut delegated_stakes), (_stake_pubkey, stake_account)| {
                        let delegation = stake_account.delegation();
                        let activation_status = delegation_activation_status(
                            delegation,
                            self.epoch,
                            &self.stake_history,
                            new_rate_activation_epoch,
                            use_fixed_point_stake_math,
                        );
                        *delegated_stakes.entry(delegation.voter_pubkey).or_default() +=
                            activation_status.effective;
                        (acc + activation_status, delegated_stakes)
                    },
                )
                .reduce(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(activation_status_a, delegated_stakes_a),
                     (activation_status_b, delegated_stakes_b)| {
                        (
                            activation_status_a + activation_status_b,
                            merge_delegated_stakes(delegated_stakes_a, delegated_stakes_b),
                        )
                    },
                )
        });
        let mut stake_history = self.stake_history.clone();
        stake_history.add(self.epoch, stake_history_entry);
        // Refresh the stake distribution of vote accounts for the next epoch,
        // using new stake history.
        let (vote_accounts, delegated_stakes) = refresh_vote_accounts(
            thread_pool,
            next_epoch,
            &self.vote_accounts,
            stake_delegations,
            &stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        let reward_epoch_delegated_stakes = RewardEpochDelegatedStakes {
            epoch: self.epoch,
            delegated_stakes: effective_delegated_stakes,
        };
        (
            stake_history,
            vote_accounts,
            delegated_stakes,
            reward_epoch_delegated_stakes,
        )
    }
```

**File:** runtime/src/stakes.rs (L504-540)
```rust
    pub(crate) fn activate_epoch(
        &mut self,
        next_epoch: Epoch,
        stake_history: StakeHistory,
        vote_accounts: VoteAccounts,
        delegated_stakes: DelegatedStakes,
    ) {
        self.epoch = next_epoch;
        self.stake_history = stake_history;
        self.vote_accounts = vote_accounts;
        self.delegated_stakes = delegated_stakes;
    }

    fn calculate_delegated_stakes(
        stake_delegations: &ImblHashMap<Pubkey, StakeAccount>,
        epoch: Epoch,
        stake_history: &StakeHistory,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) -> DelegatedStakes {
        let mut delegated_stakes = DelegatedStakes::new();
        for stake_account in stake_delegations.values() {
            let delegation = stake_account.delegation();
            let stake = delegation_effective_stake(
                delegation,
                epoch,
                stake_history,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            if stake != 0 {
                *delegated_stakes.entry(delegation.voter_pubkey).or_default() += stake;
            }
        }
        delegated_stakes
    }

```

**File:** runtime/src/stakes.rs (L617-661)
```rust
            .insert(*vote_pubkey, vote_account, calculate_delegated_stake)
    }

    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
                let old_delegation = old_stake_account.delegation();
                let old_voter_pubkey = old_delegation.voter_pubkey;
                let old_stake = delegation_effective_stake(
                    old_delegation,
                    self.epoch,
                    &self.stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if voter_pubkey != old_voter_pubkey || stake != old_stake {
                    self.sub_delegated_stake(&old_voter_pubkey, old_stake);
                    self.add_delegated_stake(voter_pubkey, stake);
                    self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
                    self.vote_accounts.add_stake(&voter_pubkey, stake);
                }
            }
        }
    }

```

**File:** runtime/src/inflation_rewards/points.rs (L272-278)
```rust
    let stake_amount = u128::from(delegation_effective_stake(
        &stake.delegation,
        epoch,
        stake_history,
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    ));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L775-794)
```rust
    }

    /// Calculates epoch rewards for stake/commission accounts
    /// Returns commission accounts, stake rewards, and the sum of all stake rewards in lamports
    #[allow(clippy::too_many_arguments)]
    fn calculate_stake_rewards_and_commissions<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        point_value: PointValue,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        metrics: &mut RewardsMetrics,
    ) -> (RewardCommissions, StakeRewardCalculation) {
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let feature_snapshot = self.feature_set.snapshot();
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
```

**File:** cli/src/stake.rs (L2734-2784)
```rust
pub async fn get_account_stake_state(
    rpc_client: &RpcClient,
    stake_account_address: &Pubkey,
    stake_account: solana_account::Account,
    use_lamports_unit: bool,
    with_rewards: Option<usize>,
    use_csv: bool,
    starting_epoch: Option<u64>,
) -> Result<CliStakeState, CliError> {
    if stake_account.owner != stake::program::id() {
        return Err(CliError::RpcRequestError(format!(
            "{stake_account_address:?} is not a stake account",
        )));
    }
    match wincode::deserialize::<StakeStateV2>(&stake_account.data) {
        Ok(stake_state) => {
            let stake_history_account = rpc_client.get_account(&stake_history::id()).await?;
            let stake_history: StakeHistory = wincode::deserialize(&stake_history_account.data)
                .map_err(|_| {
                    CliError::RpcRequestError("Failed to deserialize stake history".to_string())
                })?;
            let clock_account = rpc_client.get_account(&clock::id()).await?;
            let clock: Clock = wincode::deserialize(&clock_account.data).map_err(|_| {
                CliError::RpcRequestError("Failed to deserialize clock sysvar".to_string())
            })?;
            let new_rate_activation_epoch = get_feature_activation_epoch(
                rpc_client,
                &agave_feature_set::reduce_stake_warmup_cooldown::id(),
            )
            .await?;
            let fixed_point_activation_epoch = get_feature_activation_epoch(
                rpc_client,
                &agave_feature_set::upgrade_bpf_stake_program_to_v5_1::id(),
            )
            .await?;
            let use_fixed_point_stake_math = fixed_point_activation_epoch
                .is_some_and(|activation_epoch| clock.epoch >= activation_epoch);
            let rent_exempt_balance = rpc_client
                .get_minimum_balance_for_rent_exemption(stake_account.data.len())
                .await?;
            let mut state = build_stake_state(
                stake_account.lamports,
                &stake_state,
                use_lamports_unit,
                &stake_history,
                &clock,
                new_rate_activation_epoch,
                rent_exempt_balance,
                use_csv,
                use_fixed_point_stake_math,
            );
```
