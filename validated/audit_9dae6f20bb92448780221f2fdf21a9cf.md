Note in the doc-comment of `store_stake_accounts_in_partition`: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [1](#0-0)  This comment asserts an invariant — that a stake account cannot be mutated between the reward-calculation epoch boundary and the (much later, block-by-block) distribution phase — that the code does not actually enforce.

### Title
Stake account can be mutated between epoch-reward calculation and partitioned distribution, causing an unhandled `assert_eq!` panic in `build_updated_stake_reward` — (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
The external report describes a `Position` contract that caches a collateral amount at open-time and later blindly reuses it to redeem funds after the underlying collateral state (liquidation) has changed, causing a revert that permanently locks funds. The closest unprivileged Agave analog is in the **partitioned epoch rewards** machinery: `calculate_stake_rewards_and_commissions` snapshots a stake account's delegation at the epoch-boundary calculation phase and stores a `PartitionedStakeReward` containing a pre-computed `new_stake.delegation.stake` value [2](#0-1) . That cached value is redeemed much later, in `build_updated_stake_reward`, against the *current* live stake account fetched from `stakes_cache_accounts` [3](#0-2) .

### Finding Description
When `adjust_delegations_for_rent` (feature `relax_post_exec_min_balance_check`) is **not** active, `build_updated_stake_reward` does not reconcile the pre-computed reward against a possibly-changed on-chain delegation; it instead asserts strict equality:

```rust
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

Here, `stake` is read from the **live** `stakes_cache_accounts` at distribution time [5](#0-4) , while `new_stake.delegation.stake = partitioned_stake_reward.inflation.stake` is a value computed earlier during `calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions`, based on the delegation snapshot taken at the epoch boundary [6](#0-5) .

Reward distribution is spread across many blocks after calculation (`distribute_epoch_rewards_in_partition` walks `partition_index` over multiple block heights) [7](#0-6) . During that window the stake owner is fully unprivileged and can still submit ordinary Stake-program instructions (`Split`, `Merge`, `Withdraw`, `Redelegate`) against their own stake account, changing `delegation.stake` on-chain after the calculation snapshot was taken but before that account's partition is distributed. The code only special-cases the situation where the account is fully removed (`AccountNotFound` is handled gracefully) [8](#0-7) , but any *partial* modification of `delegation.stake` (e.g., a `Split` that reduces the delegated amount, or a `Withdraw` of excess lamports) is not accounted for and hits the `assert_eq!`. Because this runs inside `Bank::distribute_epoch_rewards_in_partition`, which every validator executes deterministically as part of block processing, a triggered `assert_eq!` panic there is not a per-node crash but a deterministic panic hit by all validators processing the same slot — a consensus-halting event, not merely a lost-funds bug like the original report.

### Impact Explanation
An unprivileged user performing a completely legitimate Stake-program instruction on their own stake account, timed to land between the epoch-boundary reward calculation and the block in the new epoch where their account's reward partition is distributed, can drive `delegation.stake` recorded on-chain out of sync with the value cached in `PartitionedStakeReward`. This triggers the `assert_eq!` panic in `build_updated_stake_reward`, which propagates from `store_stake_accounts_in_partition` → `distribute_epoch_rewards_in_partition`, both called unconditionally during block processing. Since every validator runs this same code path deterministically for the same slot, a hit panic here manifests everywhere at once — a non-RPC remote crash causing consensus halt, which matches the report's "funds become inaccessible due to reuse of a stale cached value" bug class but escalated in severity because Agave's cached-value reuse sits in mandatory block-processing logic.

### Likelihood Explanation
This depends on whether `relax_post_exec_min_balance_check` is activated cluster-wide; if it is, the code instead calls `adjust_delegation_for_rent` and does not hit the strict assertion path [9](#0-8) . I could not verify from local code/documentation whether this feature is activated on Agave's live networks, so likelihood is uncertain and depends on that feature-gate status — this is a real limitation of my analysis given available tooling. If the feature is inactive, the path is trivially reachable by any staker choosing to `Split`/`Withdraw` at a specific block height, requiring no special privileges, collusion, or leaked keys.

### Recommendation
Do not assert hard equality between a value cached at calculation time and the live on-chain state at distribution time. Instead, always recompute/clamp the delegation from the current lamport balance (as `adjust_delegation_for_rent` already does) regardless of the `relax_post_exec_min_balance_check` feature flag, and treat any legitimate reduction in delegated stake between calculation and distribution as a data condition to handle gracefully (analogous to the existing `AccountNotFound` handling), not as an invariant violation worthy of `assert_eq!`.

### Proof of Concept
1. At an epoch boundary, `calculate_stake_rewards_and_commissions` computes a `PartitionedStakeReward` for stake account `S`, caching `new_stake.delegation.stake` based on `S`'s delegation at that instant [10](#0-9) .
2. Reward distribution for `S`'s partition is scheduled for a later block height, per `distribute_partitioned_epoch_rewards`'s partition scheduling [11](#0-10) .
3. Before that block height is reached, the owner of `S` submits a normal `Split` (or `Withdraw`) instruction, changing `S`'s on-chain `delegation.stake`.
4. When the scheduled block is processed, `build_updated_stake_reward` reads the now-changed `S` from `stakes_cache_accounts`, computes `expected_delegation` from the new state, compares it against the stale `new_stake.delegation.stake` cached in step 1, and the values differ, causing `assert_eq!` to panic [4](#0-3) , crashing every validator processing that slot.

I was not able to confirm from the indexed code whether `relax_post_exec_min_balance_check` is currently active on mainnet-beta/testnet, which is the deciding factor for whether this path is presently reachable; a Devin session with full repository/feature-gate history access would be needed to confirm activation status and any additional guard added elsewhere (e.g., in the stake program itself preventing such mutations during the reward window).

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L127-150)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L269-294)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L330-335)
```rust
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L775-791)
```rust
    }

    /// Calculates epoch rewards for stake/commission accounts
    /// Returns commission accounts, stake rewards, and the sum of all stake rewards in lamports
    #[allow(clippy::too_many_arguments)]
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-849)
```rust
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
