## Title
Unbounded partitioned-epoch-rewards work per block via stake-account creation flood - ([File: runtime/src/bank/partitioned_epoch_rewards/mod.rs])

## Summary
The SEDA bug lets an attacker post many low-cost data requests that must still pass through a per-block-bounded processing queue, degrading legitimate throughput. Agave has a structurally similar bounded-per-block queue: `distribute_partitioned_epoch_rewards`, which processes exactly one "partition" of stake-account reward stores per block. The number of partitions is capped at 10% of the epoch's slots, meaning that once an attacker inflates the number of delegated stake accounts far beyond the intended per-block baseline, the *size* of each partition (not the number of blocks) grows unboundedly, forcing each block during the distribution window to do far more account-load/store work than the 400 ms-block budget the constant was designed for.

## Finding Description
`MAX_PARTITIONED_REWARDS_PER_BLOCK` documents the intended baseline: 4096 stake-account reward stores per 400 ms block [1](#0-0) .

The actual number of blocks used to distribute rewards is computed by dividing the total stake-reward count by this baseline and then **clamping** the result to at most 10% of the epoch's slots: [2](#0-1) 

This clamp means that once `total_stake_accounts` grows large enough that `num_chunks` would exceed `slots_per_epoch / 10`, the number of blocks stays capped, but the partitions produced by `hash_rewards_into_partitions` necessarily grow larger than the intended 4096-per-block baseline to still cover all the accounts within the capped block count.

Every block within the distribution window unconditionally processes exactly one partition, regardless of its size: [3](#0-2) 

Processing a partition means iterating over every stake account in `indices`, loading it from the stakes cache, doing lamport/delegation math, and finally calling `store_accounts` for the whole partition in one pass: [4](#0-3) [5](#0-4) 

Crucially, this per-block reward-distribution work is **not** subject to the transaction cost tracker / block-cost-limit machinery that governs ordinary transactions (`CostTrackerLimits`, `MAX_BLOCK_UNITS`, etc., in `runtime/src/slot_params.rs`) — it runs unconditionally as native bank state-transition logic before/around normal transaction processing, so there is no existing guard that throttles it based on the amount of work it actually performs in a given block.

An unprivileged staker can create stake accounts at the network-enforced minimum: `get_minimum_delegation` currently requires only 1 SOL of delegated stake per account plus the rent-exempt reserve, both of which are fully recoverable by the attacker after deactivation/withdrawal: [6](#0-5) 

This mirrors the SEDA primitive exactly: the "cost" of admission (aseda gas fee in SEDA; locked-then-refundable SOL here) is a user-controlled, near-fully-refundable value, and the number of admitted "requests" (stake accounts in Agave; data requests in SEDA) that must later be drained by a fixed-size, per-block-bounded processing loop is unbounded from the attacker's perspective. Just as SEDA's `expire_data_requests`/tally step must eventually process every posted request regardless of its economic value, Agave's `distribute_partitioned_epoch_rewards` must eventually process every delegated stake account's reward regardless of its (minimum) stake size, and the safety valve (clamping to 10% of the epoch) does not reduce total work — it only concentrates it, causing per-block work to exceed the constant's documented 400 ms design budget.

## Impact Explanation
If a large number of stake accounts are created and delegated by one or more accounts prior to an epoch boundary (each requiring only the minimum 1 SOL + rent-exempt reserve, fully recoverable), the partitions computed for the following epoch's reward distribution window will each contain far more entries than the `MAX_PARTITIONED_REWARDS_PER_BLOCK` = 4096 baseline that the constant states is calibrated for a 400 ms block. Every block in the distribution window (which can span up to 10% of an epoch's slots — tens of thousands of blocks) then performs proportionally larger `store_stake_accounts_in_partition` work (stakes-cache reads, per-account math, and a batched `store_accounts` call) that is not bounded by the normal cost-tracker/compute-budget limits applied to transactions. This directly matches the SEDA report's "chain slowdown" impact class: legitimate block production time is put at risk during the distribution window because the workload for the *native* reward-distribution step, unlike ordinary transactions, has no fee-market-based backpressure and no eviction of "low value" work items.

## Likelihood Explanation
Likelihood is moderate: unlike SEDA's near-zero-cost spam, an Agave attacker must lock 1 SOL + rent-exempt reserve per stake account, which raises the capital bar compared to the SEDA report. However, capital is not lost — it is recoverable after undelegation — so the attack is a temporary-capital-lockup DoS rather than a burn, exactly the same economic shape the SEDA report itself calls out ("even a negligible amount paid... will be refunded to them"). Because minimum delegation is fixed by consensus (`get_minimum_delegation`) rather than by an arbitrary attacker-chosen parameter, an attacker needs proportionally more capital than in SEDA to reach a given number of "items," but there is no upper bound anywhere in the codebase on the total number of stake accounts that can be created and simultaneously delegated, so the attack scales linearly with available (recoverable) capital.

## Recommendation
- Bound the per-block reward-distribution work by actual account count rather than only by number of blocks: instead of clamping only `num_chunks` (blocks), also cap the maximum partition size to `MAX_PARTITIONED_REWARDS_PER_BLOCK`-equivalent-per-block work, and instead extend the distribution window beyond 10% of the epoch if needed, or spread excess partitions across additional blocks even past the current cap.
- Track the reward-distribution workload in the block's cost/time budget (e.g., via `CostTrackerLimits`) so oversized partitions cannot silently exceed the intended per-slot processing time.
- Consider a per-account minimum "meaningful" stake threshold well above the raw rent-exempt/minimum-delegation floor specifically for reward-eligibility, or increase the economic cost of holding many near-minimum stake accounts (e.g. an account-count-scaled rent premium) to make the capital-lockup attack materially more expensive per "item" queued for processing.

## Proof of Concept
Not executed (index-only analysis). Conceptual PoC:
1. Prior to an epoch boundary, create N stake accounts (N in the tens of thousands), each funded with exactly `rent_exempt_reserve + get_minimum_delegation()` lamports and delegated to any active vote account.
2. At the epoch boundary, `PartitionedRewardsCalculation` computes `stake_rewards` for all N accounts; `get_reward_distribution_num_blocks` clamps the block count to `slots_per_epoch / 10`.
3. `hash_rewards_into_partitions` distributes N accounts across the clamped number of partitions, producing partitions far larger than 4096 entries.
4. Each subsequent block calls `distribute_partitioned_epoch_rewards` → `store_stake_accounts_in_partition`, iterating and storing an oversized partition in one block, exceeding the intended per-block budget documented by `MAX_PARTITIONED_REWARDS_PER_BLOCK`.
5. After the epoch, undelegate/withdraw and recover the locked capital.

### Citations

**File:** accounts-db/src/partitioned_rewards.rs (L1-10)
```rust
//! Code related to partitioned rewards distribution

/// Baseline number of stake accounts to store in one 400ms block during the
/// partitioned reward interval.
///
/// The target is 64 rewards per entry/tick. A block has a minimum of 64
/// entries/ticks, giving 4096 total rewards to store in one 400ms block. This
/// constant affects consensus; shorter slot-time targets scale this value down
/// in `Bank` state.
pub const MAX_PARTITIONED_REWARDS_PER_BLOCK: u64 = 4096;
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L137-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-360)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L366-415)
```rust
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
        }
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
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
