### Title
Assertion-based panic in partitioned epoch-reward distribution when a stake account's delegation changes between reward calculation and reward distribution - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
The Vaultka report's underlying flaw is a two-phase "request → execute" pattern where a value (deposit/withdraw amount) is computed and bounded at one point in time but consumed unchecked at a later point, after the underlying market/oracle state may have changed, with no re-validation at execution time. Agave's partitioned epoch-rewards mechanism has the same structural shape: stake rewards are *calculated* once at the epoch boundary (a "request" phase that snapshots `delegation.stake` and derives an expected post-reward value) and then *applied* many blocks later, spread across partitions (the "execute" phase). At apply time, the code reads the stake account's **current** delegation from the live `StakesCache` and asserts it must exactly equal a value derived purely from the calculation-time snapshot, with no tolerance/re-validation path — only a hard `assert!`.

### Finding Description
`calculate_stake_rewards_and_commissions` computes, once per epoch at the reward-calculation point, a `PartitionedStakeReward` for every stake delegation containing `inflation.stake` — the expected post-reward `delegation.stake` value derived from the calculation-time snapshot of the account: [1](#0-0) 

These `PartitionedStakeReward`s are stored and only *applied* to accounts later, across many separate blocks belonging to different partitions of the epoch (`store_stake_accounts_in_partition` / `build_updated_stake_reward`): [2](#0-1) 

Inside `build_updated_stake_reward`, the account is re-fetched from the **live** `StakesCache` at distribution time (i.e., its current, possibly-mutated delegation state), the calculated reward is added, and then — when `adjust_delegations_for_rent` is *not* active — the code hard-asserts that the live delegation, incremented by the calculation-time reward, exactly equals the delegation value that was computed at calculation time: [3](#0-2) 

```rust
} else {
    let expected_delegation = stake
        .delegation
        .stake
        .saturating_add(partitioned_stake_reward.inflation.stake_reward);
    assert_eq!(
        expected_delegation, new_stake.delegation.stake,
        "stake reward delegation must be consistent with the updated stake account \
         lamport balance"
    );
}
```

This is exactly the missing-bound analog of the report: the "request" (reward calculation) captures a value from account state at time T0 without constraining what legitimate state changes can occur before "execution" (distribution) at time T1. Between T0 and T1 — which spans multiple blocks because rewards are intentionally partitioned across the epoch — the stake account's owner can legitimately invoke `Split`, `Merge`, `Withdraw`, `DelegateStake`, or `Deactivate` on that very account, changing `delegation.stake`. Unlike the deposit/withdraw slippage guard in the reported bug (which fails gracefully with an error), this code path fails via `assert_eq!`, i.e., an unrecoverable panic rather than a bounded/recoverable `InstructionError`. Because reward distribution is part of normal, deterministic block processing executed identically by every validator, a state that trips this assertion would cause **every** validator applying that block to panic in the same way — not a single misbehaving node.

The comment right above the sibling commission-distribution code even acknowledges the general hazard explicitly: rewards are "intentionally deferred from calculation time" specifically because "intervening account mutations… are reflected" at distribution time, and a companion test (`test_load_and_reward_commission_accounts_reflects_vat_burn`) demonstrates the team is aware that account state legitimately drifts between calculation and distribution: [4](#0-3) 

However, the stake-side path in `build_updated_stake_reward` did not adopt the same "recompute against current state" strategy for the non-`adjust_delegations_for_rent` branch — it instead hard-asserts strict equality against the stale calculation-time value.

### Impact Explanation
If the assertion fires during real block replay, the panic is deterministic across the cluster (same block, same inputs), so it is not just a single-node crash — it is a consensus-halting event, since a large fraction (potentially all) of validators that process the offending block would panic identically. This falls squarely under the "consensus halt" / "false execution/rooting" category listed as valid impact for this scan.

### Likelihood Explanation
This requires: (1) `relax_post_exec_min_balance_check` (the `adjust_delegations_for_rent` feature) to be inactive for the relevant code path, and (2) a stake account included in a given epoch's reward set to have its `delegation.stake` legitimately altered by its owner (via a normal, permissionless stake instruction such as `Split`/`Merge`/`Withdraw`/`Deactivate`/re-`DelegateStake`) during the multi-block partitioned-distribution window before its specific partition is processed. Both conditions are plausible under ordinary validator/staker behavior with no privileged or malicious-node assumption required — an unprivileged staker performing routine stake-account management during the distribution window is enough to trigger it. The exact current activation status of `relax_post_exec_min_balance_check` in mainnet-beta could not be confirmed from the available code/index; if it is already permanently active this issue would be neutralized, but the code path and assert remain present and reachable when that feature is not active.

### Recommendation
Replace the hard `assert_eq!` in the non-`adjust_delegations_for_rent` branch of `build_updated_stake_reward` with a recoverable error path (returning a `DistributionError` variant), analogous to how `ExecuteDeposit`/`ExecuteWithdraw` handle a stale/mismatched execution value with a bounded, gracefully-failing check rather than an unconditional invariant violation. More generally, recompute or re-validate the reward-affecting delegation state at distribution time against the *live* account instead of asserting bit-for-bit equality with a value frozen at calculation time, mirroring the approach already taken for the commission-account path (deferred load + apply against current state).

### Proof of Concept
1. At the first block of a new epoch, the bank runs `calculate_rewards_for_partitioning`, snapshotting all stake delegations and producing `PartitionedStakeReward`s with `inflation.stake` computed from each account's `delegation.stake` at that moment (`calculate_stake_rewards_and_commissions`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:780-938`).
2. Reward distribution is partitioned across many subsequent blocks in the epoch (`store_stake_accounts_in_partition`, `runtime/src/bank/partitioned_epoch_rewards/distribution.rs:336-423`).
3. Before the specific partition containing a given stake account is processed, the stake account owner submits an ordinary `Split`/`Merge`/`Withdraw`/`Deactivate` transaction that changes the account's `delegation.stake` from the value it had at the calculation snapshot.
4. When that account's partition is later processed, `build_updated_stake_reward` fetches the account's *current* (mutated) delegation, adds the calculation-time reward, and (with `adjust_delegations_for_rent` inactive) hits the `assert_eq!` comparing this to the value frozen at calculation time — the values no longer match, and the process panics (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs:284-294`).
5. Because every validator executes the same deterministic block, all validators applying this block panic identically, resulting in a network-wide halt rather than an isolated crash.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L368-373)
```rust

        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-814)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L269-297)
```rust
        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-382)
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
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
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
```
