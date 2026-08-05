### Title
Stale reward-vs-delegation consistency assertion in partitioned epoch rewards distribution can panic on unprivileged stake mutation - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
This is the closest Agave analog to the reported bug class: an operation computes a value (here, a projected post-reward stake delegation) against account state observed at one point in time, then applies that stale value several blocks later without re-validating it against the account's *current* state, which an unprivileged party can mutate in between. In the `swapIdleAndAddToLiquidity` report the stale value was a `swapQuantity`; here the stale value is `partitioned_stake_reward.inflation.stake` computed during the reward-*calculation* phase and consumed several blocks later during the reward-*distribution* phase in `build_updated_stake_reward`.

### Finding Description
`calculate_stake_rewards_and_commissions` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:780-904`) snapshots each stake delegation and produces a `PartitionedStakeReward` containing a pre-computed `inflation.stake` (the expected post-reward `Stake` state) and `inflation.stake_reward` amount, based on `StakesCache` state at the reward-calculation slot [1](#0-0) .

This partitioned reward list is then spread over multiple subsequent blocks (`REWARD_CALCULATION_NUM_BLOCKS` and `num_partitions`) and applied lazily in `distribute_partitioned_epoch_rewards` -> `distribute_epoch_rewards_in_partition` -> `store_stake_accounts_in_partition` -> `build_updated_stake_reward` [2](#0-1) .

Inside `build_updated_stake_reward`, the *current* stake account is re-loaded from `stakes_cache_accounts` (i.e. its state at distribution time, which reflects any stake-program instructions the account owner executed since the calculation slot) [3](#0-2) . When the `relax_post_exec_min_balance_check` feature (`adjust_delegations_for_rent`) is *not* active, the function does not reconcile the current delegation with the stale precomputed one — it instead asserts they must match:

```
let expected_delegation = stake
    .delegation
    .stake
    .saturating_add(partitioned_stake_reward.inflation.stake_reward);
assert_eq!(
    expected_delegation, new_stake.delegation.stake,
    "stake reward delegation must be consistent with the updated stake account \
     lamport balance"
);
``` [4](#0-3) 

`stake.delegation.stake` here is the *current* value loaded from `stakes_cache_accounts`, while `new_stake.delegation.stake` is the *stale* value computed at the calculation slot. The comment immediately above `store_stake_accounts_in_partition` states the assumption underpinning this design: "Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned." [5](#0-4) 

That assumption is the exact analog of the external report's broken invariant: the rebalancer (here, the bank's reward-distribution logic) assumes the balance/state it computed a value against will still be true when it applies that value, but nothing in the searched code prevents the stake account owner from submitting an ordinary, unprivileged stake instruction (e.g. `Split`, `Merge`, `Deactivate`, `DelegateStake`/`Redelegate`) between the calculation slot and the (possibly many-block-later) distribution slot for that account's partition. I found no gate in the stake program instructions that checks the `EpochRewards` sysvar's `active` flag or otherwise blocks stake-state mutation while rewards are pending distribution for that specific account, and no such check exists in `build_updated_stake_reward`/`store_stake_accounts_in_partition` other than the `assert_eq!` itself, which fires only after the mismatch has already occurred, not to prevent it.

### Impact Explanation
If the assumption is wrong (i.e., the delegation was mutated between calculation and distribution), `assert_eq!` panics inside bank/replay-stage/reward-processing code that runs deterministically for every validator that processes that slot. Because reward distribution is part of normal block processing (not admin/plugin code) and runs identically on all validators replaying the same block, a single unprivileged account owner triggering the mismatch would cause **every validator** to panic while processing the same distribution block, resulting in a cluster-wide halt — this maps to the "consensus halt" impact bucket in the given scope. This is a Medium/High rating similar to the original finding, but the failure mode here is a deterministic panic/halt rather than a mere transaction revert, making it more severe than the original report's DoS-of-a-single-transaction issue.

### Likelihood Explanation
Likelihood is Medium: it requires (1) a stake account to be included in the current epoch's calculated stake rewards, (2) `relax_post_exec_min_balance_check` (the `adjust_delegations_for_rent` feature) to not be active for the relevant path, and (3) the account owner to submit an ordinary stake instruction that changes `delegation.stake` in the window between the calculation slot and that account's specific distribution partition slot (which can span many blocks, `REWARD_CALCULATION_NUM_BLOCKS` plus up to `num_partitions` blocks). All three conditions are plausible under normal operation since stake owners routinely split/merge/deactivate stake and have no reason to know or care about the reward-distribution schedule for their own account. I was not able to fully confirm within this investigation whether newer stake-program code paths (post the `relax_post_exec_min_balance_check`/rent-adjustment feature) close this gap entirely, or whether some other guard elsewhere (not surfaced by my searches) prevents such mutations — this should be verified against the exact feature-activation status and stake-program instruction handlers before treating this as unconditionally exploitable in the live cluster's currently active feature set.

### Recommendation
Do not `assert_eq!` on a live/current field against a value computed from a stale snapshot. Instead, when `adjust_delegations_for_rent` is false, `build_updated_stake_reward` should recompute the reward outcome based on the *current* stake account/delegation, or explicitly detect drift and gracefully fall back to a "no-op"/re-queue/burn-and-log path (as is already done for the `Err` branch in `store_stake_accounts_in_partition`), instead of using `assert_eq!`, which turns a data-consistency edge case into a validator panic. This mirrors the original report's fix of "take the smaller/consistent value" rather than assuming a stale computed value still matches current state.

### Proof of Concept
1. At epoch boundary, `calculate_stake_rewards_and_commissions` snapshots stake account `S` with `delegation.stake = X` and computes a reward `R`, producing `partitioned_stake_reward.inflation.stake.delegation.stake = X + R` for a future distribution block `B` [6](#0-5) .
2. Before block `B` is processed, the owner of `S` submits an ordinary `Stake::Split` or `Stake::Deactivate` instruction, changing `S`'s `delegation.stake` in `StakesCache` to some `Y != X`.
3. At block `B`, `store_stake_accounts_in_partition` calls `build_updated_stake_reward`, which loads the *current* `stake.delegation.stake = Y` from `stakes_cache_accounts` [3](#0-2) .
4. Because `adjust_delegations_for_rent` is false, the code computes `expected_delegation = Y + R` and asserts it equals the stale `new_stake.delegation.stake = X + R`. Since `Y != X`, `Y + R != X + R`, and the `assert_eq!` panics [4](#0-3) , crashing every validator processing block `B`.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L813-866)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-261)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L284-294)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-335)
```rust
    /// Store stake rewards in partition
    /// Returns DistributionResults containing the sum of all the rewards
    /// stored, the sum of all rewards burned, and the updated StakeRewards.
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
```
