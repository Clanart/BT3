## Analysis

The Aura report describes a public, unbounded loop (`massUpdatePools`) whose per-call cost scales with an attacker/user-growable collection (`poolInfo`), eventually exceeding the block gas limit and causing degraded/failed execution for everyone relying on that call.

The closest Agave analog by the same broken invariant — *"synchronous processing cost scales linearly with a permissionlessly-growable collection, with no cap on collection size"* — is the epoch-boundary stake-rewards calculation in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`.

### Title
Unbounded epoch-boundary reward calculation over all stake delegations enables synchronous compute-cost DoS at epoch boundary - (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
At every epoch boundary, `Bank::process_new_epoch` synchronously computes rewards for **every active stake delegation on the network** before any distribution work is spread across blocks. `calculate_stake_rewards_and_commissions` and `calculate_reward_points_partitioned` iterate (via `par_iter`, but still O(N) total work) over the full `stake_delegations` vector obtained from the stakes cache, with no upper bound on `N`. [1](#0-0) [2](#0-1) 

Unlike the *distribution* phase, which is intentionally partitioned across multiple blocks via `get_reward_distribution_num_blocks` / `partitioned_rewards_stake_account_stores_per_block` to bound per-block work, [3](#0-2) 
the *calculation* phase (`calculate_stake_rewards_and_commissions`, `calculate_reward_points_partitioned`, and the surrounding `compute_new_epoch_caches_and_rewards`/`begin_partitioned_rewards` path invoked from `process_new_epoch`) has no such partitioning or cap — it processes the entire stake-delegation set in one synchronous pass on the first block of the new epoch. [4](#0-3) 

### Finding Description
Creating a stake account is a permissionless, low-cost operation (rent-exempt minimum balance only) available to any unprivileged user/transaction sender. Every stake account with an active delegation is added to the bank's `stakes_cache` and becomes part of `stake_delegations`, the input to the reward-calculation routines. There is no protocol-level cap on the total number of stake accounts that can exist.

At the first block of every new epoch, `Bank::process_new_epoch` calls `compute_new_epoch_caches_and_rewards`, which drives `calculate_reward_points_partitioned` (computes reward points for every stake delegation) and `calculate_stake_rewards_and_commissions` (redeems rewards for every stake delegation, producing one `PartitionedStakeReward` per delegation). Both loop over the complete, attacker-growable `stake_delegations` collection synchronously, in the same code path that must complete before the bank can be considered processed for that slot: [5](#0-4) [6](#0-5) 

The code itself flags awareness of scale ("For N stake delegations, where N is >1,000,000...") but only addresses allocation overhead, not the possibility of N growing far beyond any block-time budget: [7](#0-6) 

This mirrors the Aura pattern exactly: a public/permissionless-triggerable, per-item-cost loop over an attacker-inflatable collection, with the mitigation (partitioning/rate-limiting) applied to the wrong phase — distribution is partitioned, but calculation, which runs first and must complete atomically, is not.

### Impact Explanation
If the number of live stake delegations grows large enough (via cheap, permissionless stake account creation), the calculation phase at the epoch boundary could take long enough to threaten the per-slot time budget for every validator on the network simultaneously (all validators process the same epoch-boundary bank). Because this work is mandatory and happens in lock-step across the cluster at the same epoch boundary, a sufficiently large stake-delegation count could cause widespread slot lateness/skips concentrated at every epoch boundary — a availability/consensus-degradation impact broader than a single-node DoS, since it hits all validators at the same point in time rather than a single victim.

### Likelihood Explanation
Likelihood is constrained by the real-world cost of creating and funding enough stake accounts (rent-exempt minimum per account) to push total work past a dangerous threshold, and by the fact that the computation is parallelized with `rayon`/`par_iter`, which mitigates but does not bound the total work. I was not able to fully verify (within the available tools) what the actual current mainnet-scale stake-account count is relative to the practical time budget of a slot, nor find an explicit existing cap on total stake-account count elsewhere in the codebase (`grep` for `MAX_*STAKE*`/`max_stake_accounts` found no relevant guard). This uncertainty means the finding should be treated as a scaling risk rather than a confirmed, currently-triggerable halt — it needs further empirical benchmarking (cost of N delegations vs. slot duration) to establish concrete severity, which I could not complete given tool/time constraints.

### Recommendation
- Partition (or otherwise bound the per-block/per-call work of) the reward *calculation* phase the same way the distribution phase already is, e.g., process stake delegations in bounded chunks across multiple blocks/slots rather than in one synchronous pass at the epoch boundary.
- Add an explicit cap/metric-based circuit breaker on total stake-delegation count considered per epoch-boundary calculation, with overflow handled via deferred/streamed processing.
- Benchmark `calculate_stake_rewards_and_commissions`/`calculate_reward_points_partitioned` against realistic upper bounds of network-wide stake-account growth to confirm/deny whether current thread-pool parallelism keeps this within the slot time budget.

### Proof of Concept
Conceptual (not executed, given read-only/ask-mode constraints and lack of local benchmarking tools):
1. Submit a large number of low-cost `CreateAccount` + `Initialize`/`DelegateStake` transactions to create many stake accounts, each above the minimal activation stake, over enough epochs so they all become "active" delegations in the `stakes_cache`.
2. At the next epoch boundary, observe `Bank::process_new_epoch` → `compute_new_epoch_caches_and_rewards` → `calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions` processing time scale with the injected delegation count.
3. Measure whether processing time for this single, non-partitioned phase approaches or exceeds the slot duration budget as delegation count grows, which would manifest as synchronized slot lateness across the entire validator set at every epoch boundary.

**Uncertainty note:** I was unable to confirm (from local code alone) the exact current per-delegation cost or where/whether an equivalent cap exists elsewhere in cluster-wide stake-account admission (e.g., via minimum delegation size checks in the stake program) that might already substantially limit `N` in practice. This should be validated with a full Devin session capable of running benchmarks/tests against the actual codebase.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-820)
```rust
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
        let delay_commission_updates = feature_snapshot.delay_commission_updates;
        let commission_rate_in_basis_points = feature_snapshot.commission_rate_in_basis_points;
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let block_revenue_sharing = feature_snapshot.block_revenue_sharing;

        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L942-1002)
```rust
    fn calculate_reward_points_partitioned<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: &Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: &CachedVoteAccounts<'_>,
        epoch_inflation_rewards: u64,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        metrics: &RewardsMetrics,
    ) -> Option<PointValue> {
        let CachedVoteAccounts {
            distribution_epoch_vote_accounts,
            ..
        } = cached_vote_accounts;

        let solana_vote_program: Pubkey = solana_vote_program::id();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        match ag_epoch_type {
            AlpenglowEpochType::Alpenglow { .. } => {
                // In alpenglow, we do not need to compute `PointValue::points` as the final
                // rewards are simply the total credits stored in the vote account.  We just need
                // to return a `Some` value with valid rewards.
                return Some(PointValue {
                    rewards: epoch_inflation_rewards,
                    points: 0,
                });
            }
            AlpenglowEpochType::Tower => {
                // For tower we need to compute the valid `PointValue::points`.
            }
            AlpenglowEpochType::MigrationEpoch { .. } => {
                // For the migrating epoch, we need to compute the tower portion of `PointValue::points`.
            }
        }

        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        let (points, measure_us) = measure_us!(thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .map(|(_stake_pubkey, stake_account)| {
                    let vote_pubkey = stake_account.delegation().voter_pubkey;

                    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey)
                    else {
                        return 0;
                    };
                    if vote_account.owner() != &solana_vote_program {
                        return 0;
                    }

                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
                })
                .sum::<u128>()
        }));
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

**File:** runtime/src/bank.rs (L1841-1872)
```rust
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

        // Distribute rewards commission to vote accounts and cache stake rewards
        // for partitioned distribution in the upcoming slots.
        let (epoch_rewards, begin_partitioned_rewards_time_us) =
            measure_us!(self.begin_partitioned_rewards(
                parent_epoch,
                parent_slot,
                parent_height,
                &rewards_calculation,
                &mut rewards_metrics,
                thread_pool,
            ));
```
