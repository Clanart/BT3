Audit Report

## Title
Unbounded, unpartitioned epoch-boundary stake-reward calculation allows unprivileged stakers to stall block production - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

## Summary
At every epoch boundary, `Bank::process_new_epoch` triggers `calculate_stake_rewards_and_commissions` and `calculate_reward_points_partitioned`, both of which iterate over the full set of stake delegations via `stake_delegations.par_iter()` in a single synchronous pass on the block-production path. The reward *distribution* phase that follows is explicitly chunked across multiple blocks via `get_reward_distribution_num_blocks`, but the calculation phase has no equivalent bound, and the delegation set can be grown cheaply given a `get_minimum_delegation` of as little as 1 lamport.

## Finding Description
`calculate_stake_rewards_and_commissions` and `calculate_reward_points_partitioned` both operate on `stake_delegations: Vec<(&Pubkey, &StakeAccount<Delegation>)>` with `.par_iter()`, processing every delegation in the network in one call, as confirmed in the current code [1](#0-0)  and [2](#0-1) . The in-code comment explicitly acknowledges unbounded scale ("For N stake delegations, where N is >1,000,000") while relying purely on rayon parallelism rather than an upper bound on N [3](#0-2) .

By contrast, the reward *distribution* phase is deliberately chunked across multiple blocks via `get_reward_distribution_num_blocks`, which caps per-block work using `partitioned_rewards_stake_account_stores_per_block` and clamps the interval to 10% of an epoch's slots [4](#0-3) . No analogous cap exists for the calculation phase.

The floor on economic cost to grow the delegation set is `get_minimum_delegation`, which returns 1 lamport unless `upgrade_bpf_stake_program_to_v5` is active (in which case it is 1 SOL), confirmed verbatim in the current codebase [5](#0-4) .

## Impact Explanation
This computation runs deterministically on every validator during the epoch-boundary slot's bank creation, not on an offline/administrative path. An attacker-inflated delegation count therefore imposes identical extra work on every validator (leader and replaying/voting validators alike), which is a plausible network-wide slowdown vector at epoch boundaries rather than a single-node degradation. This matches the "consensus halt" / "non-RPC remote exhaustion/crash" impact category, since it is triggered purely by ordinary, unprivileged stake/delegate transactions.

## Likelihood Explanation
Likelihood is fundamentally an economic/scale question that I cannot fully resolve through static code review alone: the code confirms the *mechanism* (unbounded O(N) `par_iter()` loop with no cap, unlike the chunked distribution phase) and the *minimum cost floor* (`get_minimum_delegation` = 1 lamport pre-feature-activation, 1 SOL post-activation), but whether this floor makes attacks economically infeasible under current network configuration (feature activation status, mainnet rent parameters, actual rayon-parallel throughput at realistic core counts) requires runtime measurement/benchmarking that is outside the scope of this static review. The claim's own framing acknowledges this uncertainty ("required scale is uncertain but not proven impossible"), and I could not verify current mainnet feature-activation state or empirically measure the wall-clock cost of processing very large delegation counts.

## Recommendation
Bound or partition the reward *calculation* phase analogously to the distribution phase — e.g., cap delegations processed per epoch-boundary block, or split `calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions` across multiple slots the way `get_reward_distribution_num_blocks` already does for distribution. Alternatively, scale the effective cost of creating additional stake delegations (e.g., delegation-count-aware minimum delegation, or a cap on total live stake accounts) so that O(N) epoch-boundary work stays bounded by a known constant.

## Proof of Concept
1. Submit many `CreateAccount` + `Initialize` + `DelegateStake` transactions from independent wallets, each funding a stake account with only `rent_exempt_reserve + get_minimum_delegation(...)` lamports [6](#0-5) .
2. Accumulate a very large number of such minimal delegations before an epoch boundary.
3. At the next epoch boundary, observe `calculate_stake_rewards_and_commissions` and `calculate_reward_points_partitioned` process the entire delegation set in one unpartitioned call [7](#0-6) .
4. Measure wall-clock time scaling with delegation count and compare against the chunked distribution phase's bound [4](#0-3)  to confirm the asymmetry between bounded distribution and unbounded calculation. Actual measurement of the threshold at which this causes observable slot delays was not performed in this review and would require a running test-validator or benchmark harness.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-819)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L977-1002)
```rust
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

**File:** runtime/src/stake_utils.rs (L15-27)
```rust
/// The minimum stake amount that can be delegated, in lamports.
/// When this feature is added, it will be accompanied by an upgrade to the BPF Stake Program.
/// NOTE: This is also used to calculate the minimum balance of a delegated stake account,
/// which is the rent exempt reserve _plus_ the minimum stake delegation.
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
