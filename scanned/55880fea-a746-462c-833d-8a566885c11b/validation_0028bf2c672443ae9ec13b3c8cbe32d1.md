Based on my investigation, I found a strong local analog to the stETH "point‑in‑time balance vs. later state" bug pattern inside Agave's partitioned epoch‑rewards machinery.

### Title
Stale stake-delegation snapshot used at reward distribution time can panic all validators (deterministic assert failure) - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Like the stETH vault bug — where a point-in-time balance recorded at deposit time is blindly reused at refund time without accounting for state changes in between — Agave's epoch-rewards system calculates a stake account's expected post-reward `delegation.stake` value once, at the reward **calculation** phase (epoch boundary), and then, several blocks later during the **distribution** phase, reloads the *live* stake account and asserts that `live_delegation.stake + reward == calculated_delegation.stake`. If the live delegation value diverges from the value assumed at calculation time, the code does not gracefully handle the mismatch — it panics via `assert_eq!`.

### Finding Description
`calculate_stake_rewards_and_commissions` / `redeem_delegation_rewards` compute `PartitionedStakeReward.inflation.stake` (the post‑reward `Stake` struct, including `delegation.stake`) using a snapshot of stake delegations taken at the epoch boundary [1](#0-0) .

This calculated value is cached and used possibly many blocks later (`REWARD_CALCULATION_NUM_BLOCKS` + partitioned distribution across multiple slots) in `build_updated_stake_reward`, which reloads the **current** stake account from `stakes_cache_accounts` (i.e., the live chain state at distribution time, not the calculation-time snapshot): [2](#0-1) 

When the `relax_post_exec_min_balance_check` feature (`adjust_delegations_for_rent`) is inactive, the code does not reconcile any divergence — it asserts that the live delegation plus the reward exactly equals the previously calculated delegation: [3](#0-2) 

The surrounding comments explicitly acknowledge that state can drift between calculation and distribution — e.g. `redeem_delegation_rewards` notes "delegation for stake {stake_pubkey} may be adjusted at distribution, unless lamports are transferred before distribution block" [4](#0-3)  — and `recalculate_stake_rewards` separately documents that recalculated values (used after snapshot restore) can diverge from calculation-time state for the *same reason* (commission accounts loaded from "the current bank, and not the start of the epoch") [5](#0-4) . This is the exact bug class from the report: an absolute value recorded at time T1 is trusted at time T2 without accounting for state drift.

The `adjust_delegations_for_rent` code path (used when the feature *is* active) exists specifically to reconcile such drift instead of asserting equality [6](#0-5) , which itself confirms the drift scenario is a known, real possibility — the assert-based branch is simply the unmitigated legacy path.

### Impact Explanation
If the live `delegation.stake` for a given stake account differs from the value assumed during the epoch-boundary calculation phase (e.g., due to a `Split`/`Merge`/`Deactivate`/`Redelegate` stake-program instruction changing the delegation between calculation and the (multi-block) distribution window), `assert_eq!` fires. Because `distribute_partitioned_epoch_rewards` runs deterministically inside `Bank::new_from_parent` on every validator processing the same slot, a triggered panic is not a single-node crash — it is a **simultaneous, deterministic panic across the entire validator set**, i.e. a consensus halt, not merely a node crash.

### Likelihood Explanation
Likelihood depends on whether ordinary stake-owner instructions (`Split`, `Merge`, `Deactivate`, etc.) can execute against a stake account during the epoch reward calculation→distribution window (which spans multiple blocks) and change `delegation.stake` while a payout for that same account is still pending. The stake program itself is not part of the indexed portion of this repository that I could inspect, so **I could not directly confirm or rule out** whether the stake-program instruction processor blocks mutation of a stake account while its epoch reward is queued for distribution. This is the key open question and should be verified before treating this as a confirmed, exploitable path — the assert path is real and reachable in the reward-distribution code, but whether an unprivileged actor can actually get the guard-less mismatch to occur depends on external protections not visible here.

### Recommendation
- Confirm whether the stake program prevents mutating instructions on stake accounts with a pending/unpaid partitioned reward; if not, add such a guard.
- Replace the `assert_eq!` (legacy, non-`adjust_delegations_for_rent` path) with the same reconciliation logic used by `adjust_delegation_for_rent`, so a legitimate but unexpected divergence degrades gracefully (e.g., recomputing/clamping the delegation) instead of panicking the validator.
- More generally, apply the same principle the external report suggests for stETH: instead of caching an absolute point-in-time value (`delegation.stake`) that is asserted equal later, cache a derived quantity (e.g., only the reward delta) and always recompute the final state from the *live* account at distribution time.

### Proof of Concept
Not independently reproducible with local evidence alone because the stake-program-side guard (if any) against modifying a stake account with pending distribution rewards could not be located in this repository slice. The reachable code path is:
1. At an epoch boundary, `calculate_stake_rewards_and_commissions` computes `PartitionedStakeReward.inflation.stake` for account `S` based on its `delegation.stake` at that moment [7](#0-6) .
2. Reward distribution for `S` is deferred to a later block via partitioning [8](#0-7) .
3. If `S`'s live `delegation.stake` changes before its partition is processed (via a stake-program instruction), `build_updated_stake_reward`'s `assert_eq!` fails when `adjust_delegations_for_rent` is inactive [3](#0-2) , panicking every validator processing that slot identically.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L663-696)
```rust
                if delegation_may_need_adjustment(
                    stake.delegation.stake,
                    stake.delegation.stake,
                    current_lamports,
                    minimum_lamports,
                    status,
                ) {
                    debug!(
                        "delegation for stake {stake_pubkey} may be adjusted at distribution, \
                         unless lamports are transferred before distribution block"
                    );
                    let inflation = InflationReward {
                        stake,
                        stake_reward: 0,
                        commission_bps: (!custom_commission_collector).then_some(0),
                    };
                    // Set `is_vote_account` to `false` in order to deliberately
                    // fail during commission collector checks. This avoids
                    // creating a reward entry during payout.
                    let reward_commission = RewardCommission {
                        commission_bps: (!custom_commission_collector).then_some(0),
                        commission_lamports: 0,
                        burned_lamports: 0,
                        is_vote_account: false,
                    };
                    return Some(InflationRewardWithCommission {
                        inflation,
                        commission_pubkey: vote_pubkey,
                        reward_commission,
                    });
                } else {
                    debug!("delegation for stake {stake_pubkey} will not be adjusted");
                    return None;
                }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-850)
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

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1069-1075)
```rust
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L127-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-297)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

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
