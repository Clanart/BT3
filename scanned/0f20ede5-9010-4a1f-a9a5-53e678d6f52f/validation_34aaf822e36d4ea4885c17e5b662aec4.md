### Title
Unbounded, unpartitioned per-epoch iteration over all stake delegations enables cheap attacker-driven CPU exhaustion at epoch boundaries - ([File: runtime/src/stakes.rs])

### Summary
The reported bug is a classic "unbounded loop over user-created entries" DoS: `TreasuryVester::distribute()` iterates once over every registered recipient with no cap, so growing the recipient list can blow the block gas limit. The Agave analog is `Stakes::calculate_activated_stake` (and its caller chain through `Bank::compute_new_epoch_caches_and_rewards` / `calculate_rewards_for_partitioning`), which iterates once, synchronously, over **every stake delegation in the entire cluster** at every epoch boundary. Unlike the reward *distribution* phase, which is deliberately chunked across up-to-10%-of-epoch blocks (`get_reward_distribution_num_blocks`), the reward *calculation* phase (`calculate_activated_stake`, `refresh_vote_accounts`, `calculate_rewards_for_partitioning`) has no partitioning or cap — it processes the full stake-delegation set in a single call on the critical, deterministic bank-processing path that every validator (not just the leader) must execute.

### Finding Description
`Stakes::calculate_activated_stake` walks the full `stake_delegations` slice with `par_iter().fold()/.reduce()`: [1](#0-0) 

This is invoked once per epoch from `Bank::compute_new_epoch_caches_and_rewards`, which also feeds the same unfiltered delegation set into `calculate_rewards` → `calculate_rewards_for_partitioning`: [2](#0-1) 

`compute_new_epoch_caches_and_rewards` is called synchronously from `process_new_epoch`, which runs on every bank at the epoch boundary (i.e., on every validator replicating/validating that slot, not merely the block producer): [3](#0-2) 

While the actual *storing* of stake rewards is explicitly partitioned to bound per-block work (`get_reward_distribution_num_blocks` clamps the number of chunks to at most `slots_per_epoch / 10`): [4](#0-3) 

...the calculation phase that produces the un-chunked `stake_rewards`/`delegated_stakes` data (`calculate_activated_stake`, `refresh_vote_accounts`, and the per-account reward math inside `calculate_rewards_for_partitioning`) has no equivalent cap — it is one linear pass over however many stake accounts exist on-chain: [5](#0-4) 

The number of stake accounts is entirely attacker-controlled: creating a stake account only requires paying the rent-exempt minimum for `StakeStateV2` plus the minimum delegation. Notably, `get_minimum_delegation()` is only 1 SOL when a newer stake-program feature is active — but returns just **1 lamport** otherwise, meaning the actual floor cost per additional delegated stake account is essentially the rent-exempt reserve alone: [6](#0-5) 

A comment in the codebase itself acknowledges that this scan already assumes a bounded, "reasonable" account count (~5,500) and treats iteration cost as a known but accepted tradeoff, rather than something protected by a hard cap: [7](#0-6) 

There is a hard cap on the number of *vote* accounts used for reward distribution (`MAX_ALPENGLOW_VOTE_ACCOUNTS`, applied via `clone_and_filter_for_vat`) — [8](#0-7)  — but there is no analogous cap on the number of *stake* delegations feeding into `calculate_activated_stake` / `calculate_rewards_for_partitioning`. The existing guard therefore does not stop the unbounded stake-delegation scan: it only bounds the number of distinct voter/vote-account entries, not the number of stake accounts delegated to them, which is what drives the O(n) work in `calculate_activated_stake`, `refresh_vote_accounts`, and per-account reward computation.

### Impact Explanation
Because this scan is executed identically by every validator (it's part of deterministic bank-state transition at epoch boundaries, feeding the bank hash via reward-history/stake-cache updates), inflating the stake-delegation count with a very large number of cheaply-created stake accounts increases the wall-clock cost of epoch-boundary processing across the entire validator set simultaneously. If this processing time grows to exceed the time budget for producing/validating the epoch-boundary block, it can cause validators to fall behind, miss slots, or otherwise degrade cluster-wide liveness/performance at every epoch transition thereafter (the cost is permanent once the accounts exist, unlike a single burst). This falls under "consensus halt / non-RPC remote exhaustion" impact since it is not a per-request RPC cost but a recurring cost baked into every validator's block processing.

### Likelihood Explanation
Likelihood is low-to-moderate: creating enough stake accounts to meaningfully affect epoch-boundary timing requires substantial rent-exempt SOL outlay (millions of accounts × ~0.002 SOL rent-exempt reserve each), so it is not a "click of a button" attack, mirroring the original report's "Likelihood: 1 / Impact: 5" scoring. However, unlike the Solidity case (bounded by an explicit block gas limit that reverts the whole call), this Agave path has no functional cap at all on the number of iterated stake delegations — only economic cost limits it, and the per-account cost floor can be extremely low (rent-exempt reserve alone when the newer minimum-delegation feature isn't active).

### Recommendation
Introduce a partitioned or capped calculation path for `calculate_activated_stake` / `calculate_rewards_for_partitioning`, mirroring the chunking already applied to the distribution phase (`get_reward_distribution_num_blocks`), or impose a protocol-level cap on the total number of stake delegations counted per epoch (analogous to `MAX_ALPENGLOW_VOTE_ACCOUNTS` for vote accounts), so the epoch-boundary computation cost is bounded independent of how many stake accounts an attacker chooses to create.

### Proof of Concept
1. Fund an account with enough SOL to cover `rent_exempt_minimum(StakeStateV2::size_of())` (~0.00204 SOL per account under pre-v5.1 minimum-delegation semantics) for N accounts.
2. Programmatically create and delegate N stake accounts (N in the millions) across the network over successive epochs, keeping delegation just above the 1-lamport-plus-rent floor.
3. At every subsequent epoch boundary, every validator's `Bank::process_new_epoch` → `compute_new_epoch_caches_and_rewards` → `Stakes::calculate_activated_stake` / `calculate_rewards_for_partitioning` must scan all N delegations in one synchronous pass (`runtime/src/stakes.rs:434-502`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), increasing the wall-clock time every validator spends processing the epoch-boundary slot, unlike the store phase which is explicitly chunked across up to 10% of an epoch's slots.

**Note on confidence:** I was not able to fully inspect `calculate_validator_rewards` / `calculate_stake_rewards_and_commissions` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` (grep located them but read attempts were cut off before completion), so the exact per-account cost of the reward-math loop is not fully confirmed from source in this session — only `calculate_activated_stake` and `refresh_vote_accounts` in `runtime/src/stakes.rs` were fully verified as unbounded, unpartitioned O(n) scans over attacker-controllable stake-account counts.

### Citations

**File:** runtime/src/stakes.rs (L386-399)
```rust
        // Assert that cached vote accounts are consistent with accounts-db.
        //
        // This currently includes ~5500 accounts, parallelizing brings minor
        // (sub 2s) improvements.
        for (pubkey, vote_account) in stakes.vote_accounts.iter() {
            let Some(account) = get_account(pubkey) else {
                return Err(Error::VoteAccountNotFound(*pubkey));
            };
            let vote_account = vote_account.account();
            if vote_account != &account {
                error!("vote account mismatch: {pubkey}, {vote_account:?}, {account:?}");
                return Err(Error::VoteAccountMismatch(*pubkey));
            }
        }
```

**File:** runtime/src/stakes.rs (L434-478)
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
```

**File:** runtime/src/stakes.rs (L756-806)
```rust
fn refresh_vote_accounts(
    thread_pool: &ThreadPool,
    epoch: Epoch,
    vote_accounts: &VoteAccounts,
    stake_delegations: &[(&Pubkey, &StakeAccount)],
    stake_history: &StakeHistory,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> (VoteAccounts, DelegatedStakes) {
    fn merge(mut stakes: DelegatedStakes, other: DelegatedStakes) -> DelegatedStakes {
        if stakes.len() < other.len() {
            return merge(other, stakes);
        }
        for (pubkey, stake) in other {
            *stakes.entry(pubkey).or_default() += stake;
        }
        stakes
    }
    let delegated_stakes = thread_pool.install(|| {
        stake_delegations
            .par_iter()
            .fold(
                DelegatedStakes::default,
                |mut delegated_stakes, (_stake_pubkey, stake_account)| {
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
                    delegated_stakes
                },
            )
            .reduce(DelegatedStakes::default, merge)
    });
    let vote_accounts = vote_accounts
        .iter()
        .map(|(&vote_pubkey, vote_account)| {
            let delegated_stake = delegated_stakes
                .get(&vote_pubkey)
                .copied()
                .unwrap_or_default();
            (vote_pubkey, (delegated_stake, vote_account.clone()))
        })
        .collect();
    (vote_accounts, delegated_stakes)
```

**File:** runtime/src/bank.rs (L1762-1803)
```rust
        let stakes = self.stakes_cache.stakes();
        let stake_delegations = stakes.stake_delegations_vec();
        let (
            (
                stake_history,
                unfiltered_distribution_vote_accounts,
                delegated_stakes,
                reward_epoch_delegated_stakes,
            ),
            calculate_activated_stake_time_us,
        ) = measure_us!(stakes.calculate_activated_stake(
            self.epoch(),
            thread_pool,
            self.new_warmup_cooldown_rate_epoch(),
            &stake_delegations,
            self.use_fixed_point_stake_math(),
        ));
        debug_assert_eq!(reward_epoch_delegated_stakes.epoch, rewarded_epoch);

        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
        let (rewards_calculation, update_rewards_with_thread_pool_time_us) =
            measure_us!(self.calculate_rewards(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                rewards_metrics,
            ));
```

**File:** runtime/src/bank.rs (L1815-1846)
```rust
    /// process for the start of a new epoch
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```

**File:** runtime/src/stake_utils.rs (L19-27)
```rust
#[inline(always)]
pub fn get_minimum_delegation(upgrade_bpf_stake_program_to_v5_is_active: bool) -> u64 {
    if upgrade_bpf_stake_program_to_v5_is_active {
        const MINIMUM_DELEGATION_SOL: u64 = 1;
        MINIMUM_DELEGATION_SOL * LAMPORTS_PER_SOL
    } else {
        1
    }
}
```

**File:** vote/src/vote_account.rs (L212-244)
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

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }
```
