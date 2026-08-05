This confirms the minimum stake delegation is `1 SOL` [1](#0-0)  — a permissionless, unprivileged actor can create an unbounded number of stake accounts and delegate them at this minimum cost, growing a global list that a required epoch-boundary consensus operation must fully traverse in a single, unpartitioned pass.

### Title
Unbounded synchronous iteration over all stake delegations during epoch-boundary reward calculation - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Every epoch boundary, Agave computes stake rewards by iterating over `Stakes::stake_delegations_vec()` — the full, network-wide set of active stake-to-vote-account delegations — in a single, non-partitioned pass inside the block that starts the new epoch. Unlike the reward *distribution* phase, which is deliberately spread across many blocks via `get_reward_distribution_num_blocks`/`partitioned_rewards_stake_account_stores_per_block`, the reward *calculation* phase has no partitioning or hard upper bound on the number of stake delegations processed. Since creating and delegating a stake account is a permissionless action requiring only the minimum delegation (1 SOL) plus rent-exempt reserve, an unprivileged actor can grow this list arbitrarily large over time, inflating the mandatory, synchronous, whole-network computation that every validator must perform at every epoch boundary — directly analogous to the NFTX `_feeReceivers` unbounded-iteration bug class.

### Finding Description
At the start of every epoch, `Bank::compute_new_epoch_caches_and_rewards` collects **all** stake delegations via `stakes.stake_delegations_vec()` [2](#0-1)  and passes them into `calculate_rewards` → `calculate_rewards_for_partitioning` [3](#0-2) .

That function calls `calculate_reward_points_partitioned`, which does a full `par_iter()` walk over the entire `stake_delegations` slice to sum reward points [4](#0-3) , and `calculate_stake_rewards_and_commissions`, which again walks the **entire** `stake_delegations` vector — one entry per stake account network-wide — to redeem rewards for every single delegation [5](#0-4) . The code's own comment acknowledges this can scale to N > 1,000,000 delegations [6](#0-5) .

Crucially, while the *storage* of computed rewards is explicitly partitioned across up to 10% of an epoch's slots via `get_reward_distribution_num_blocks` [7](#0-6)  and `distribute_partitioned_epoch_rewards` [8](#0-7) , the *calculation* step that must precede it has no equivalent partitioning: it must complete in full before the first epoch-boundary block can be produced/replayed, because `calculate_rewards` caches and blocks on this single synchronous computation via `epoch_rewards_calculation_cache.lock()` [9](#0-8) .

Growing this list is entirely permissionless: anyone can create a stake account and delegate the protocol minimum of 1 SOL plus rent-exempt reserve [10](#0-9) , so the size of `stake_delegations_vec()` is attacker-influenceable and has no protocol-enforced ceiling. This is the same broken invariant as the NFTX report: an unprivileged party can grow a list that a required, non-skippable protocol function must fully traverse, and the existing guard (parallelism via `rayon`, `with_min_len(500)`) only reduces wall-clock cost by a constant factor — it does not bound total work, unlike the explicit partitioning used for distribution.

### Impact Explanation
If the number of stake delegations grows large enough (via mass-creation of minimum-sized stake accounts), the epoch-boundary calculation work performed synchronously by *every* validator (since replay must reproduce identical bank state) could exceed the time budget of a single slot/epoch-boundary block. Because this computation is identical and unavoidable for all validators simultaneously (not just the leader), a sufficiently large delegation count could cause widespread degraded block production or replay lag exactly at epoch boundaries across the whole cluster — a liveness/consensus-timing risk rather than an isolated single-node crash, matching the "consensus halt" / non-RPC remote exhaustion category.

### Likelihood Explanation
Growing the stake-delegation count is cheap per unit (1 SOL minimum delegation + rent-exempt reserve, refundable via deactivation/withdrawal) but requires substantial aggregate capital and rent to reach a scale (millions of accounts) that meaningfully threatens epoch-boundary timing, and the code is already parallelized with rayon across all CPU cores, which raises the practical bar significantly. I could not find any explicit protocol-level cap on the total number of stake accounts/delegations in the code reviewed, nor any partitioning of the calculation phase analogous to the distribution phase, so likelihood is bounded mainly by economic cost/creation-rate limits rather than a code-level guard.

### Recommendation
Apply the same partitioning strategy used for reward *distribution* to reward *calculation*: either (a) cap/rate-limit growth of active stake delegations, or (b) restructure `calculate_reward_points_partitioned` / `calculate_stake_rewards_and_commissions` to process stake delegations incrementally across multiple blocks (as already done for `store_stake_accounts_in_partition`), so no single block's processing time scales unboundedly with the total number of stake accounts in existence.

### Proof of Concept
1. Repeatedly submit permissionless `CreateAccount` + `Initialize`/`DelegateStake` instructions to create the minimum viable stake account (rent-exempt reserve + 1 SOL delegation, per `get_minimum_delegation`) [1](#0-0) , targeting any active vote account, across many unique stake account addresses.
2. Over successive epochs, allow these delegations to activate, growing `Stakes::stake_delegations` to a very large count.
3. At the next epoch boundary, `Bank::compute_new_epoch_caches_and_rewards` must synchronously call `calculate_rewards_for_partitioning`, which iterates the entire delegation set in `calculate_reward_points_partitioned` and `calculate_stake_rewards_and_commissions` [11](#0-10)  before any block in the new epoch can be produced or replayed — measure `calculate_activated_stake_time_us` / `update_rewards_with_thread_pool_time_us` [12](#0-11)  to observe growth in per-epoch-boundary compute time as delegation count increases, and confirm no partitioning bound exists analogous to `get_reward_distribution_num_blocks` [7](#0-6)  for this calculation step.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L329-346)
```rust
        let mut epoch_rewards_calculation_cache =
            self.epoch_rewards_calculation_cache.lock().unwrap();
        let rewards_calculation = epoch_rewards_calculation_cache
            .entry(self.parent_hash)
            .or_insert_with(|| {
                Arc::new(self.calculate_rewards_for_partitioning(
                    stake_history,
                    stake_delegations,
                    cached_vote_accounts,
                    rewarded_epoch,
                    reward_epoch_delegated_stakes,
                    reward_calc_tracer,
                    thread_pool,
                    metrics,
                ))
            })
            .clone();
        drop(epoch_rewards_calculation_cache);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L471-518)
```rust
    pub(super) fn calculate_rewards_for_partitioning<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        reward_epoch_delegated_stakes: RewardEpochDelegatedStakes,
        reward_calc_tracer: Option<impl Fn(&RewardCalculationEvent) + Send + Sync>,
        thread_pool: &ThreadPool,
        metrics: &mut RewardsMetrics,
    ) -> PartitionedRewardsCalculation {
        let capitalization = self.capitalization();
        let epoch_inflation_rewards =
            if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
                EpochInflationAccountState::new_from_bank(self)
                    .and_then(|state| state.inflation_rewards_for_epoch(rewarded_epoch))
                    .unwrap_or_else(|| {
                        panic!(
                            "Missing epoch inflation state for non-Tower reward epoch \
                             {rewarded_epoch}"
                        )
                    })
            } else {
                self.calculate_epoch_inflation_rewards(capitalization, rewarded_epoch)
            };
        // `distribution_epoch_vote_accounts` is the post-VAT-filter snapshot
        // produced upstream of this call, so its length is the right value for
        // the `epoch_rewards` metric.
        let num_filtered_vote_accounts =
            cached_vote_accounts.distribution_epoch_vote_accounts.len();

        let CalculateValidatorRewardsResult {
            reward_commissions,
            stake_reward_calculation: stake_rewards,
            point_value,
        } = self
            .calculate_validator_rewards(
                stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                epoch_inflation_rewards,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                metrics,
            )
            .unwrap_or_default();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L804-808)
```rust
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L813-1009)
```rust
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
                    let maybe_reward_record = self.redeem_delegation_rewards(
                        rewarded_epoch,
                        stake_pubkey,
                        stake_account,
                        &point_value,
                        stake_history,
                        &cached_vote_accounts,
                        reward_calc_tracer.as_ref(),
                        new_warmup_cooldown_rate_epoch,
                        delay_commission_updates,
                        commission_rate_in_basis_points,
                        adjust_delegations_for_rent,
                        ag_epoch_type,
                        custom_commission_collector,
                        use_fixed_point_stake_math,
                    );

                    let (reward, maybe_reward_record) = match (block_reward, maybe_reward_record) {
                        (0, None) => (None, None),
                        (_, Some(res)) => {
                            let InflationRewardWithCommission {
                                inflation,
                                commission_pubkey,
                                reward_commission,
                            } = res;
                            let stake_reward = inflation.stake_reward;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation,
                                    block_reward,
                                }),
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: Some((commission_pubkey, reward_commission)),
                                }),
                            )
                        }
                        (_, None) => {
                            // Create a zero entry for distribution
                            let stake = *stake_account.stake();
                            let stake_reward = 0;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation: InflationReward {
                                        stake,
                                        stake_reward,
                                        commission_bps: None,
                                    },
                                    block_reward,
                                }),
                                // Need a reward record for accumulator
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: None,
                                }),
                            )
                        }
                    };
                    // It's important that for every stake delegation, we write
                    // a value to the cell of the stake rewards vector,
                    // regardless of whether it's `Some` or `None` variant.
                    // This allows us to pre-allocate the vector with the known
                    // size and avoid re-allocations, which were the bottleneck
                    // in this path.
                    reward_ref.write(reward);
                    maybe_reward_record
                })
                .fold(
                    RewardsAccumulator::default,
                    |mut rewards_accumulator, accumulation| {
                        rewards_accumulator.add_reward(accumulation);
                        rewards_accumulator
                    },
                )
                .reduce(
                    RewardsAccumulator::default,
                    |rewards_accumulator_a, rewards_accumulator_b| {
                        rewards_accumulator_a.accumulate_into_larger(rewards_accumulator_b)
                    },
                )
        });
        let RewardsAccumulator {
            reward_commissions,
            num_stake_rewards,
            total_stake_rewards_lamports,
        } = rewards_accumulator;
        // SAFETY: We initialized all the `stake_rewards` elements up to
        // `stake_delegations_len` (one cell per delegation, `Some` or `None`).
        // `num_stake_rewards` is the count of the `Some` cells.
        unsafe {
            stake_rewards.assume_init(num_stake_rewards, stake_delegations_len);
        }
        measure_redeem_rewards.stop();
        metrics.redeem_rewards_us = measure_redeem_rewards.as_us();

        (
            reward_commissions,
            StakeRewardCalculation {
                stake_rewards: Arc::new(stake_rewards),
                total_stake_rewards_lamports,
            },
        )
    }

    /// Calculates epoch reward points from stake/vote accounts.
    /// Returns reward lamports and points for the epoch or none if points == 0.
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
        metrics.calculate_points_us.fetch_add(measure_us, Relaxed);

        (points > 0).then_some(PointValue {
            rewards: epoch_inflation_rewards,
            points,
        })
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L80-149)
```rust
    pub(in crate::bank) fn distribute_partitioned_epoch_rewards(&mut self) {
        let EpochRewardStatus::Active(status) = &self.epoch_reward_status else {
            return;
        };

        let distribution_starting_block_height = match &status {
            EpochRewardPhase::Calculation(status) => status.distribution_starting_block_height,
            EpochRewardPhase::Distribution(status) => status.distribution_starting_block_height,
        };

        let height = self.block_height();
        if height < distribution_starting_block_height {
            return;
        }

        if let EpochRewardPhase::Calculation(status) = &status {
            // epoch rewards have not been partitioned yet, so partition them now
            // This should happen only once immediately on the first rewards distribution block, after reward calculation block.
            let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
            let (partition_indices, partition_us) = measure_us!({
                epoch_rewards_hasher::hash_rewards_into_partitions(
                    &status.all_stake_rewards,
                    &epoch_rewards_sysvar.parent_blockhash,
                    epoch_rewards_sysvar.num_partitions as usize,
                )
            });

            // update epoch reward status to distribution phase
            self.set_epoch_reward_status_distribution(
                distribution_starting_block_height,
                Arc::clone(&status.all_stake_rewards),
                partition_indices,
            );

            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("partition_us", partition_us, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );
        }

        let EpochRewardStatus::Active(EpochRewardPhase::Distribution(partition_rewards)) =
            &self.epoch_reward_status
        else {
            // We should never get here.
            unreachable!(
                "epoch rewards status is not in distribution phase, but we are trying to \
                 distribute rewards"
            );
        };

        let distribution_end_exclusive =
            distribution_starting_block_height + partition_rewards.partition_indices.len() as u64;

        assert!(
            self.epoch_schedule.get_slots_in_epoch(self.epoch)
                > partition_rewards.partition_indices.len() as u64
        );

        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```
