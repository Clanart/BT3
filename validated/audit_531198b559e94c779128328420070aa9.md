## Title
Partitioned Epoch-Reward Recalculation Uses Live `StakesCache` State Instead of Reward-Epoch-Boundary Stake, Corrupting Individual Stake-Reward Payouts - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Agave's partitioned epoch-reward distribution can span many blocks/forks. If the calculation needs to be redone mid-distribution (`recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards`), it re-derives each stake account's `Delegation` from the **current** `StakesCache` of the bank at the time of recalculation rather than from a snapshot of the delegation as it stood at the reward-epoch boundary. This is the same broken-invariant class described in the Babylon report: using a live/current mutable value where a historical, period-scoped value is required for a reward that has already logically accrued.

### Finding Description
`recalculate_stake_rewards` is invoked when `EpochRewards` sysvar is still `active` (i.e., partitioned distribution is mid-flight) to recompute pending stake rewards: [1](#0-0) 

It pulls delegation data straight from `self.stakes_cache.stakes()` — the bank's live stakes cache at the moment recalculation runs — via `get_epoch_params_for_recalculation(rewarded_epoch, &stakes)`, then feeds those `Delegation` structs into `calculate_stake_rewards_and_commissions`, the exact same function used for the original epoch-boundary calculation: [2](#0-1) 

The reward math (`redeem_delegation_rewards` → `calculate_stake_points_and_credits` → `delegation_effective_stake`) computes each delegator's effective stake for `rewarded_epoch` by warming/cooling `delegation.stake` (the current target amount) through `stake_history`. This correctly handles warmup/cooldown ratios for `rewarded_epoch`, but it does **not** protect against the delegation's target `stake` field itself having changed between the original epoch-boundary snapshot and the point of recalculation. A delegator who performs a `Split`, `Merge`, `Delegate`, or `Deactivate` operation on their own stake account during the distribution window changes `delegation.stake`/`activation_epoch`/`deactivation_epoch` in the live `StakesCache`, and the next recalculation will silently substitute this new value in place of what was active during `rewarded_epoch`.

The developers are explicitly aware that "loaded from current bank, not start of epoch" is unsound — they call this out for reward-commission accounts and disclaim their use: [3](#0-2) 

But that same current-bank-state problem is not disclaimed for the `stake_rewards` field that recalculation actually uses to drive real payouts, only for `RewardCommissionAccounts`. The existing regression test only verifies that the **AG delegated-stake denominator** (`RewardEpochDelegatedStakes`, a genuinely frozen per-epoch snapshot) stays consistent across recalculation: [4](#0-3) 

It does not cover the case where an individual delegator's own `Delegation.stake` is mutated (via a normal, permissionless stake-program instruction) between the initial calculation and a mid-distribution recalculation — there is no guard rail comparable to the fixed `RewardEpochDelegatedStakes` snapshot for the numerator side of the calculation.

### Impact Explanation
An ordinary staker (no special privilege needed) can perform a stake operation (e.g. `Split`/`Merge`/`Deactivate`/redelegate) on their own stake account that lands between the point rewards for an epoch are first calculated and a subsequent recalculation trigger (fork switch causing `recalculate_partitioned_rewards_if_active` to run again for a still-active `EpochRewards` sysvar). The recalculated reward for that stake account — and potentially for that vote account's block-reward numerator computed against a stale `pending_delegator_rewards` — is derived from the post-modification `Delegation`, not the historical one for which the reward was earned. This causes incorrect (larger or smaller) reward payouts, i.e., unfair/incorrect fund distribution consistent with the "false execution/acceptance" and fund-loss impact classes for `runtime`/`accounts` bugs.

### Likelihood Explanation
Recalculation only triggers when the `EpochRewards` sysvar is still `active`, i.e., partitioned distribution has not finished across all partitions/blocks and a new bank recomputes the pending stake rewards (e.g., on fork switches or restarts during the distribution window). The stake-account mutation itself requires no special access — any delegator can split/merge/deactivate their own stake at any time. The combination (distribution window still open + delegator modifies their own stake before recalculation happens) is a normal, permissionless sequence rather than a contrived attack, making this moderately likely to occur, though it is timing-dependent on distribution duration and fork/restart cadence.

### Recommendation
Snapshot the `Delegation` (stake amount, activation/deactivation epochs) used for reward calculation at the reward-epoch boundary — the same way `RewardEpochDelegatedStakes` freezes the total delegated stake for that epoch — and have `recalculate_stake_rewards` consult that frozen per-account snapshot instead of `self.stakes_cache.stakes()` (current live state) when it needs to redo the computation mid-distribution.

### Proof of Concept
1. Epoch `N` ends; `calculate_stake_rewards_and_commissions` computes stake rewards for delegator `D` based on `D`'s `Delegation` at that moment (stake = `S1`), and starts partitioned distribution (`EpochRewards` sysvar `active = true`).
2. Before all partitions are distributed, `D` submits a `Split`/`Deactivate` stake instruction that changes `D`'s live `Delegation.stake` to `S2 ≠ S1`.
3. A fork switch or bank restart occurs while the sysvar is still `active`, triggering `recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:1015-1095`).
4. `recalculate_stake_rewards` reads `D`'s `Delegation` from the current `stakes_cache` (now reflecting `S2`), recomputes `D`'s stake reward for `rewarded_epoch = N` using `S2` instead of the historically-correct `S1`.
5. `D` (or other delegators sharing the same vote account, via the shared point-value/denominator) receives a reward amount inconsistent with what they actually earned during epoch `N`.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1058)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1063-1075)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1076-1094)
```rust
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
        (stake_rewards, partition_indices)
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2770-2797)
```rust
        assert!(unpaid_reward.inflation.stake_reward > 0);

        // Force exactly one stake reward to be distributed before simulating
        // snapshot restore. That write updates StakesCache with a larger
        // delegation for the same vote account.
        bank.set_epoch_reward_status_distribution(
            bank.block_height(),
            Arc::clone(&original_stake_rewards),
            vec![vec![paid_index], vec![unpaid_index]],
        );
        bank.distribute_partitioned_epoch_rewards();

        let epoch_rewards_sysvar = bank.get_epoch_rewards_sysvar();
        assert!(epoch_rewards_sysvar.active);
        let (recalculated_stake_rewards, _partition_indices) =
            bank.recalculate_stake_rewards(&epoch_rewards_sysvar, &thread_pool);
        let recalculated_unpaid_reward = recalculated_stake_rewards
            .enumerated_rewards_iter()
            .find_map(|(_index, reward)| {
                (reward.stake_pubkey == unpaid_reward.stake_pubkey).then_some(reward)
            })
            .expect("unpaid stake reward must still be pending after recalculation");

        assert_eq!(
            unpaid_reward.inflation.stake_reward, recalculated_unpaid_reward.inflation.stake_reward,
            "recalculation after partial distribution must use the same AG delegated stake \
             denominator as the original epoch-boundary calculation"
        );
```
