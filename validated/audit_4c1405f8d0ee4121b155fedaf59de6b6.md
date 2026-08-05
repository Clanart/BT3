## Title
Reward-recalculation denominator/numerator mismatch permanently strands stake/block rewards after mid-distribution stake removal - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `runtime/src/inflation_rewards/points.rs`, `runtime/src/alpenglow_epoch_type.rs`)

### Summary
The reported bug pattern is a classic "denominator includes more than the numerator" accounting flaw: a fixed total (`empirePoints`) is used as the divisor while the actual amount paid out (`playerEmpirePoints`, unlocked only) is smaller, so a portion of the pot is never paid and is stranded. Agave's Alpenglow partitioned-epoch-rewards code has the same structural pattern: a per-vote-account pot (`pending_delegator_rewards`) is divided by a stake total (`total_active_stake` / `RewardEpochDelegatedStakes::delegated_stakes`) that is snapshotted once at the epoch boundary, while the numerator (each delegation's `stake_amount`) is recomputed later, per-partition, from the live `StakesCache`. If a delegation is fully removed from `StakesCache` before its partition is processed (which spans multiple blocks during which normal, permissionless stake-withdraw/deactivate transactions can execute), its contribution disappears from the numerator side of every subsequent calculation while the fixed denominator snapshot still contains its stake, so the fraction of the pot attributable to that delegation is never distributed to anyone and is left un-credited.

### Finding Description
Reward distribution for a rewarded epoch is split across many blocks/partitions for scalability:

- `distribute_reward_commissions` / `begin_partitioned_rewards` snapshot a `PartitionedRewardsCalculation` (with `point_value`, i.e. `{rewards, points}`) once at epoch-boundary time. [1](#0-0) 

- For Alpenglow, `RewardEpochDelegatedStakes::delegated_stakes` (the per-vote-account stake denominator, i.e. the "total points"/"empirePoints" analog) is computed once, by summing each delegation's *effective* stake at the epoch boundary, and stored: [2](#0-1) 

- That fixed snapshot is later reused, unmodified, both for the AG "block reward" split: [3](#0-2) 
and for the AG inflation "points" split (where, for Alpenglow, `points` *are* the actual lamport reward, per the code comment "In alpenglow, `points` represents the actual reward that this `stake` earned"): [4](#0-3) [5](#0-4) 

- Rewards are not computed once — they are *recomputed on demand* for every subsequent partition from whatever delegations currently exist in the live `StakesCache`: [6](#0-5) 

- The code itself acknowledges that live mutation of the numerator's inputs, without adjusting the fixed denominator, produces a mismatch — but only patches this for the `block_reward` path with an explicit clamp: [7](#0-6) 
The equivalent AG inflation-points calculation (`calculate_alpenglow_points`) contains no such clamp or reconciliation logic — it simply computes `earned_credits * stake_amount / total_stake` from whatever `stake_amount` currently exists for that pubkey: [8](#0-7) 

Because reward-distribution partitions are spread over `REWARD_CALCULATION_NUM_BLOCKS`+ blocks (a normal, multi-block window), ordinary permissionless transactions (stake withdrawal, full deactivation past cooldown, merges, etc.) can remove a delegation from `stakes_cache.stake_delegations()` before its partition is reached. `recalculate_stake_rewards` only iterates *currently existing* delegations pulled from `get_epoch_params_for_recalculation`, so a delegation that vanished from the cache contributes **zero** to the recalculated numerator for any partition computed after its removal, while `epoch_rewards_sysvar.total_points` / `RewardEpochDelegatedStakes.delegated_stakes` (the fixed denominator, unchanged since epoch boundary) still contains that removed delegation's original stake. This exactly mirrors the reported Solidity bug's broken invariant: **denominator counts value that the numerator no longer distributes to anyone**, so lamports that were budgeted (and already accounted for in `capitalization`/`EpochRewards.total_rewards`, per the `create_epoch_rewards_sysvar` assertion `point_value.rewards >= distributed_rewards`) are never paid out to any staker and are effectively stranded — either left "credited" in the vote account's `pending_delegator_rewards` field or simply omitted from `distributed_rewards`, without any compensating burn/incinerator accounting path visible in this calculation.

### Impact Explanation
This causes real, unprivileged loss/misallocation of validator/staker inflation rewards: lamports that the protocol has already reserved and accounted for as "to be distributed" for an epoch are not credited to any account, because the fixed stake-total denominator no longer matches the live numerator set once a delegation exits the `StakesCache` mid-distribution. This is not a cosmetic accounting error — it directly changes fund distribution outcomes for real SOL, without requiring a malicious validator, malicious peer, or any privileged actor; it is triggered purely by an ordinary staker choosing to withdraw/deactivate their stake during the (multi-block) reward-distribution window, which is entirely within their rights and is a routine, permissionless action.

### Likelihood Explanation
High likelihood of occurrence in principle, given reward distribution deliberately spans many blocks (`REWARD_CALCULATION_NUM_BLOCKS`, `num_partitions`) specifically to allow interleaved normal transaction processing, and stake deactivation/withdrawal is an extremely common, unrestricted staker operation. The engineers were clearly aware of a related class of drift (the `block_reward` clamp comment explicitly discusses "if stake account has already received rewards, it's possible to have `stake > total_active_stake`"), which shows the underlying assumption ("denominator stays consistent with numerator across partitions") is already known to be fragile; the equivalent safeguard is visibly absent from the AG-points path. However, I was not able to fully trace, within the available tool budget, whether `get_epoch_params_for_recalculation` filters/handles removed delegations in a way that reconciles the sysvar's `distributed_rewards` counter against `total_rewards` at the very end of distribution (there may be a final "sweep" or burn step not seen in the excerpts reviewed), so the exact terminal fate of the "missing" fraction (silently stranded vs. eventually reconciled/burned) is uncertain and would need direct code/runtime verification.

### Recommendation
Either (a) recompute `RewardEpochDelegatedStakes`/`total_stake` denominators to exclude stake that has exited `StakesCache` by the time each partition is processed (so the live numerator and denominator stay consistent, analogous to "Option 2" unlocking-based fix in the original report), or (b) snapshot the full numerator set (not just the denominator) at epoch boundary so that recalculation always operates over the original, complete delegation set regardless of subsequent withdrawals, applying any subsequent stake changes only to lamport amounts, not membership. Add an explicit end-of-distribution reconciliation/assertion comparing `total_rewards` against the sum of everything actually paid (`distributed_rewards` + reward-commissions + any burn), and route any leftover remainder to a well-defined destination (e.g., incinerator) rather than allowing it to be silently unaccounted for.

### Proof of Concept
Conceptual reproduction path (not executed, based on static code review):
1. Roll into an Alpenglow (or migration) epoch boundary; `RewardEpochDelegatedStakes::delegated_stakes[vote_pubkey]` is snapshotted as the sum of effective stake for all current delegations to that vote account, per `Stakes::calculate_activated_stake`. [2](#0-1) 
2. Reward calculation/distribution begins and is split across `num_partitions` blocks (`begin_partitioned_rewards`, `set_epoch_reward_status_distribution`). [9](#0-8) 
3. Before the partition containing a given delegation is reached, the delegator submits an ordinary `Deactivate`+`Withdraw` sequence that fully removes the stake account from `stakes_cache.stake_delegations()`.
4. When `recalculate_stake_rewards` runs for that later partition, it re-derives `stake_delegations` from the *current* `StakesCache` via `get_epoch_params_for_recalculation`, so the withdrawn delegation is absent and contributes nothing to `calculate_alpenglow_points`'s numerator computation. [10](#0-9) 
5. `RewardEpochDelegatedStakes.delegated_stakes[vote_pubkey]` (the denominator used inside `calculate_alpenglow_points`) is never adjusted for this removal, so `earned_credits * stake_amount / total_stake` for every *remaining* delegation to that vote account still divides by the original, too-large `total_stake`, and the withdrawn delegation's share of `pending_delegator_rewards` is paid to no one. [8](#0-7) 

This is offered as a bug-class analog derived from static analysis of the local repository; I could not execute the code or fully trace whether a later reconciliation step exists to recover the stranded amount, so this should be validated with a concrete integration test (create N delegations to one vote account, fully withdraw one delegation mid-distribution across partitions, and assert `sum(paid rewards) == point_value.rewards` for that vote account) before treating it as confirmed.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
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
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-296)
```rust
    pub(in crate::bank) fn begin_partitioned_rewards(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_block_height: u64,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> u64 {
        let RewardCommissionLamportAmounts {
            distributed_lamports,
            distributed_to_incinerator_lamports,
            burned_lamports,
        } = self.distribute_reward_commissions(
            parent_epoch,
            rewards_calculation,
            rewards_metrics,
            thread_pool,
        );

        let slot = self.slot();
        let distribution_starting_block_height =
            self.block_height() + REWARD_CALCULATION_NUM_BLOCKS;

        let PartitionedRewardsCalculation {
            stake_rewards,
            point_value,
            ..
        } = rewards_calculation;

        let stake_rewards = Arc::clone(&stake_rewards.stake_rewards);

        let num_partitions = self.get_reward_distribution_num_blocks(&stake_rewards);
        self.set_epoch_reward_status_calculation(distribution_starting_block_height, stake_rewards);

        self.create_epoch_rewards_sysvar(
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            distribution_starting_block_height,
            num_partitions,
            point_value,
            0, // block_rewards
        );

        datapoint_info!(
            "epoch-rewards-status-update",
            ("start_slot", slot, i64),
            ("calculation_block_height", self.block_height(), i64),
            ("active", 1, i64),
            ("parent_slot", parent_slot, i64),
            ("parent_block_height", parent_block_height, i64),
        );
        distributed_lamports
            + rewards_calculation
                .stake_rewards
                .total_stake_rewards_lamports
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L392-414)
```rust
        let StakeRewardCalculation {
            total_stake_rewards_lamports,
            ..
        } = stake_rewards;

        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
        info!(
            "distributed reward commissions: {} out of {}, remaining {}",
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            point_value.rewards,
            total_stake_rewards_lamports
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L960-968)
```rust
            AlpenglowEpochType::Alpenglow { .. } => {
                // In alpenglow, we do not need to compute `PointValue::points` as the final
                // rewards are simply the total credits stored in the vote account.  We just need
                // to return a `Some` value with valid rewards.
                return Some(PointValue {
                    rewards: epoch_inflation_rewards,
                    points: 0,
                });
            }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1088)
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
```

**File:** runtime/src/stakes.rs (L446-502)
```rust
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

**File:** runtime/src/inflation_rewards/points.rs (L236-301)
```rust
/// Calculate alpenglow points for `stake` based on the vote account's `reward_epoch_credits`
///
/// This value is the lamports paid to the vote account * `stake_amount` / `vote_account_stake`
/// `vote_account_stake` is fetched from the precomputed `reward_epoch_delegated_stakes` for the
/// reward epoch
///
/// Returns (alpenglow points, new_credits_observed)
fn calculate_alpenglow_points(
    stake: &Stake,
    reward_epoch_credits: Option<(Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
    reward_epoch_delegated_stakes: &RewardEpochDelegatedStakes,
) -> Result<(u128, u64), CalculatedStakePoints> {
    let Some((epoch, final_epoch_credits, initial_epoch_credits)) = reward_epoch_credits else {
        return Ok((0, stake.credits_observed));
    };
    if epoch != reward_epoch_delegated_stakes.epoch {
        // In this case, the vote account did not record any credits in this epoch
        // The latest entry is from a prior epoch - thus the delegation gets 0 rewards
        return Ok((0, stake.credits_observed));
    }

    let (earned_credits, new_credits_observed) = {
        let mut new_credits_observed = stake.credits_observed;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        (earned_credits, new_credits_observed)
    };

    let stake_amount = u128::from(delegation_effective_stake(
        &stake.delegation,
        epoch,
        stake_history,
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    ));

    let earned_points = if earned_credits == 0 || stake_amount == 0 {
        0
    } else {
        let Some(total_stake) = reward_epoch_delegated_stakes
            .delegated_stakes
            .get(&stake.delegation.voter_pubkey)
            .copied()
            .filter(|stake| *stake != 0)
        else {
            record_error(format!(
                "AG delegated stake denominator for vote_pubkey={} in epoch={} failed",
                stake.delegation.voter_pubkey, reward_epoch_delegated_stakes.epoch
            ));
            return Err(CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed,
                force_credits_update_with_skipped_reward: true,
            });
        };
        earned_credits * stake_amount / total_stake as u128
    };
```
