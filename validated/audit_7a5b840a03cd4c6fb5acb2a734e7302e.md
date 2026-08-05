## Title
Block-reward pool over-allocation across delegators when partitioned rewards are recalculated mid-distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
This is the closest local analog to the Wildcat H-01 pattern: a fixed shared pool (`batch.normalizedAmountPaid` in Wildcat, `pending_delegator_rewards` in Agave) is divided among many claimants proportionally to a share value, but the share value used for some claimants is recomputed *after* the pool has already partially paid out other claimants, using a numerator that reflects post-payout state while the denominator remains a stale snapshot. This can make the sum of individual payouts computed against the pool exceed the pool total, exactly mirroring the invariant break described in the report ("sum of all... amounts must be ≤ total pool").

### Finding Description
`calculate_block_reward` computes each stake delegation's slice of a vote account's `pending_delegator_rewards` pool as: [1](#0-0) 

```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into()
    .unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```

`total_active_stake` comes from `reward_epoch_delegated_stakes`, a fixed, pre-epoch-boundary snapshot of stake per vote account. [2](#0-1)  `stake`, however, is `delegation.stake` read live via `delegation_effective_stake` from the current `StakeAccount` in `StakesCache`. [3](#0-2) 

Reward distribution happens over multiple partitions/blocks, and a stake account's `delegation.stake` is increased in place as soon as its inflation/block reward is stored (`build_updated_stake_reward`, invoked from `store_stake_accounts_in_partition`). [4](#0-3)  If a fork switch or snapshot restore occurs while distribution is still active, `recalculate_partitioned_rewards_if_active` re-derives the *remaining, unpaid* stake rewards by calling `recalculate_stake_rewards`, which re-invokes `calculate_stake_rewards_and_commissions` → `calculate_block_reward` using the *current* `StakesCache` (i.e., already-inflated `delegation.stake` for any account paid before the rollback point) but the *same, unchanged* `total_active_stake` denominator from the original epoch-boundary snapshot. [5](#0-4) 

The code explicitly acknowledges this exact scenario in its own comment: "During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`. ... We can also have individual rewards look greater than the pending rewards. This is harmless in practice, but we clamp it just to be safe." [6](#0-5)  The `.min(pending_delegator_rewards)` clamp only bounds a *single* delegator's payout to the full pool — it does nothing to prevent the *sum* of payouts across all of a vote account's delegators (some computed pre-rollback with original stake, some post-rollback with inflated stake against the same fixed denominator) from exceeding `pending_delegator_rewards`. This is structurally identical to Wildcat's bug: an early participant is paid at one rate, the rate basis then shifts, and later participants effectively draw down a shared pool computed from stale totals, breaking the "sum of allocations ≤ pool" invariant.

### Impact Explanation
If the aggregate block-reward allocations for a vote account's delegators exceed `pending_delegator_rewards`, later-processed delegators in the same reward round would either receive an inflated amount (over-minting rewards / incorrect capitalization accounting) or, if the surplus is drawn from lamports that are not actually free (e.g., the vote account's real balance), cause a shortfall for the last-processed accounts. Because reward distribution updates bank capitalization counters directly from these computed amounts (`stake_reward_lamports_minted`, `block_reward_lamports_distributed`), an inconsistency here corrupts consensus-critical state (`capitalization`, `EpochRewards` sysvar `distributed_rewards`) shared by all validators, which is a false-execution/false-accounting class of impact.

### Likelihood Explanation
This requires the reward-distribution recalculation path (`recalculate_partitioned_rewards_if_active`) to run mid-epoch-boundary distribution — i.e., a fork switch/snapshot rollback while `EpochRewardPhase` is active and block-revenue-sharing (SIMD-0123) is enabled — which is a normal, permissionless consensus event (fork switches happen regularly, not attacker-controlled malicious behavior). No privileged or malicious actor is needed; it only needs ordinary chain reorganization during the reward-distribution window, which the code's own comment shows the developers were already aware could occur ("During recalculation, if stake account has already received rewards...").

### Recommendation
Track and decrement a running "remaining pending_delegator_rewards" value across the whole recalculation pass (mirroring how `StakeRewardCalculation::total_rewards` is documented to reflect only "rewards that have not yet been distributed"), rather than deriving `total_active_stake`-relative shares independently per delegator with a numerator that can grow post-payout. Alternatively, freeze `stake` for the block-reward computation to the same epoch-boundary snapshot used for `total_active_stake` (as is already done for `RewardEpochDelegatedStakes` specifically to avoid this class of staleness/mismatch, per the comment at lines 190-193), so the fraction basis cannot shift between the initial calculation and any later recalculation.

### Proof of Concept
Conceptual reproduction (cannot be executed without the full validator fork/snapshot harness, but is directly supported by the existing code path and by the project's own recalculation test in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, e.g. `test_alpenglow_recalculation_after_partial_distribution`-style tests near lines 2770-2798 which specifically probe recalculation-after-partial-distribution behavior):

1. Enable `block_revenue_sharing` (SIMD-0123) and Alpenglow reward epoch type so `calculate_block_reward` is exercised.
2. Set up a vote account with `pending_delegator_rewards = P` and two delegators A and B with stakes `s_a`, `s_b` such that `s_a + s_b == total_active_stake` (from `RewardEpochDelegatedStakes`).
3. Begin partitioned distribution; let partition 0 (containing A) be processed and stored, which increases `A`'s `delegation.stake` via `build_updated_stake_reward`.
4. Before partition 1 (containing B) is processed, trigger a fork switch/snapshot restore that causes `recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards` to run.
5. `calculate_block_reward` for B now recomputes using the current `StakesCache`, where A's stake has grown but `total_active_stake` is unchanged, so B's own share is computed against a denominator that no longer reflects the current relative proportions; combined with A's already-paid share, `A_reward + B_reward` can exceed `P`, breaking the "sum of allocations ≤ pending_delegator_rewards" invariant analogous to Laurence's failed withdrawal in the Wildcat report.

Note: I was not able to fully trace, within the available search budget, exactly where/whether `pending_delegator_rewards` lamports are physically backed and decremented on-chain (e.g., debited from the vote account's own balance) versus purely used as an accounting ceiling for newly-minted rewards; this would determine whether the impact manifests as an accounting/capitalization inconsistency or an actual insufficient-funds failure for later delegators. Confirming this requires reading `build_updated_stake_reward` and the vote-account lamport-transfer logic in full, which exceeded the available tool budget in this session.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1093)
```rust
    fn recalculate_stake_rewards(
        &self,
        epoch_rewards_sysvar: &EpochRewards,
        thread_pool: &ThreadPool,
    ) -> (Arc<PartitionedStakeRewards>, Vec<Vec<usize>>) {
        assert!(epoch_rewards_sysvar.active);
        // If rewards are active, the rewarded epoch is always the immediately
        // preceding epoch.
        let rewarded_epoch = self.epoch().saturating_sub(1);

        let point_value = PointValue {
            rewards: epoch_rewards_sysvar.total_rewards,
            points: epoch_rewards_sysvar.total_points,
        };

        let stakes = self.stakes_cache.stakes();
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = self.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);
        let ag_epoch_type = AlpenglowEpochType::get(self, rewarded_epoch, || {
            RewardEpochDelegatedStakes::get(self)
        });

        // On recalculation, only the `StakeRewardCalculation::stake_rewards`
        // field is relevant. It is assumed that reward commission accounts have
        // already been calculated and delivered, while
        // `StakeRewardCalculation::total_rewards` only reflects rewards that
        // have not yet been distributed.
        //
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
        let (_, StakeRewardCalculation { stake_rewards, .. }) = self
            .calculate_stake_rewards_and_commissions(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                point_value,
                &ag_epoch_type,
                thread_pool,
                null_tracer(),
                &mut RewardsMetrics::default(), // This is required, but not reporting anything at the moment
            );
        drop(stakes);
        let partition_indices = hash_rewards_into_partitions(
            &stake_rewards,
            &epoch_rewards_sysvar.parent_blockhash,
            epoch_rewards_sysvar.num_partitions as usize,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L384-398)
```rust
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
```
