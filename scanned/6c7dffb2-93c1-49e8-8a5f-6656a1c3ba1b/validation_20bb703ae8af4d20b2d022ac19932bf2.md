## Note on scope and confidence

I could not find definitive evidence within the indexed code of whether stake-program instructions (`Split`, `Merge`, `Deactivate`, `Redelegate`) are explicitly blocked from mutating a stake account's `Delegation::stake` while that account's epoch reward has already been *calculated* but not yet *distributed* (partitioned rewards span multiple blocks). The comment in `distribution.rs` explicitly assumes this can't happen ("stake accounts are checked in calculation, and further state mutation prevent[ed] by stake-program restrictions"), but I was unable to locate the actual restriction in `programs/stake/`. Given the exact structural match to the report's bug class (a value computed against a frozen/prior state that is invalidated by legitimate state mutation before finalization), I present this as the closest analog with that caveat.

### Title
Stake delegation mutated between epoch-reward calculation and distribution triggers a hard `assert_eq!` panic in `Bank::build_updated_stake_reward` - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
The external report's broken invariant is: a value (payout) is computed against an account's state at one point in time (frozen), the account's state changes before that computed value is applied (unfreeze), and the code does not reconcile the discrepancy — producing an inconsistent/lost result. In Agave, the epoch-reward pipeline computes each stake account's expected post-reward delegation during the *calculation* phase, then applies it at *distribution* time up to several blocks later. `Bank::build_updated_stake_reward` reloads the *live* stake account and asserts that `stake.delegation.stake + reward == new_stake.delegation.stake` computed earlier [1](#0-0) . If the account's on-chain `delegation.stake` diverges from what was assumed at calculation time (e.g., due to legitimate stake-program activity between the two phases), this assertion fails and panics.

### Finding Description
`build_updated_stake_reward` is invoked once per stake account during each distribution-phase block, days/blocks after the corresponding reward was computed in `redeem_delegation_rewards` during the calculation phase [2](#0-1) . At distribution time, the bank re-fetches the *current* stake account from `StakesCache` [3](#0-2)  and, when the rent-adjustment feature path is not taken, hard-asserts that the live delegation plus the previously calculated reward exactly equals the previously calculated `new_stake.delegation.stake`:
```rust
let expected_delegation = stake.delegation.stake.saturating_add(partitioned_stake_reward.inflation.stake_reward);
assert_eq!(expected_delegation, new_stake.delegation.stake, ...);
``` [1](#0-0) 

This is functionally the same class of bug as the report: a "snapshot" of the account (its frozen/calculated state) is used to compute a payout, but the underlying account is mutable in the interim (across `REWARD_CALCULATION_NUM_BLOCKS` and the partition-distribution window, which can span up to 10% of an epoch's slots per `get_reward_distribution_num_blocks` [4](#0-3) ). The code comment acknowledges the risk exists but relies on an unverified assumption ("further state mutation prevent[ed] by stake-program restrictions") [5](#0-4)  rather than an enforced guard at the stake-program layer, similar to how `resolveUser` in the external report assumed frozen state implicitly without verifying/reconciling it.

Separately, `recalculate_stake_rewards` (used after snapshot restore) independently recomputes rewards from the live `StakesCache` [6](#0-5) , and its own regression test explicitly demonstrates that stake delegation state can shift between original calculation and a later recalculation pass tied to the *same* vote account [7](#0-6) , confirming that the underlying `stakes_cache` state is not immutable across the reward-distribution window from the bank's own perspective.

### Impact Explanation
If a live stake account's delegation can legitimately diverge from the value assumed at calculation time within the distribution window (e.g., via redelegate/split/merge/deactivate performed by the stake owner, a permissionless action requiring no special privilege), the `assert_eq!` in `build_updated_stake_reward` will panic. Because this code runs inside `Bank::distribute_epoch_rewards_in_partition`, which is executed deterministically by every validator while replaying/producing the same block [8](#0-7) , a panic here is not a localized crash — it is a deterministic failure hit by all validators processing that slot, i.e., a network-wide consensus halt, which falls squarely within the "Valid Impact" categories (false execution/rooting/acceptance, consensus halt).

### Likelihood Explanation
Likelihood depends entirely on whether the stake program actually permits `Delegation::stake` (or `credits_observed`) to be modified by an unprivileged staker between calculation and distribution without going through the tracked reward-recrediting path. I was unable to confirm or rule this out from the available index (no explicit "epoch rewards active" guard was found in `programs/stake/`). This is a documented internal assumption in the runtime code itself, not a proven guard, which is the same posture that produced the original Solidity bug (an unverified assumption about frozen/consistent state).

### Recommendation
Verify explicitly (rather than assume) that no permissionless stake-program instruction can change `Delegation::stake` for an account with a reward already computed and pending distribution. If such a path exists, either: (1) snapshot and re-validate stake state transactionally at distribution time and gracefully reconcile/skip (as already done for the `adjust_delegations_for_rent` path) instead of hard-panicking, or (2) add an explicit stake-program-level restriction preventing instructions that mutate `Delegation::stake` for accounts with a pending/uncredited partitioned reward, mirroring the `pending_delegator_rewards` guard already implemented for vote-account withdrawals [9](#0-8) .

### Proof of Concept
I was not able to construct or confirm an end-to-end PoC transaction sequence from the indexed code alone, because I could not confirm the missing guard in `programs/stake/`. A verifying engineer should: (1) create a stake account with rewards computed during the calculation phase of `begin_partitioned_rewards`, (2) before the corresponding distribution block is reached (i.e., during the multi-block distribution window sized by `get_reward_distribution_num_blocks`), submit a stake instruction (`Split`, `Merge`, `Deactivate`, or `DelegateStake`/`Redelegate`) that changes `Delegation::stake` for that account, and (3) advance to the distribution block for that account's partition and observe whether `build_updated_stake_reward`'s `assert_eq!` at `runtime/src/bank/partitioned_epoch_rewards/distribution.rs:289-293` panics.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-149)
```rust
impl Bank {
    /// Process reward distribution for the block if it is inside reward interval.
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L610-649)
```rust
    #[expect(clippy::too_many_arguments)]
    fn redeem_delegation_rewards(
        &self,
        rewarded_epoch: Epoch,
        stake_pubkey: &Pubkey,
        stake_account: &StakeAccount<Delegation>,
        point_value: &PointValue,
        stake_history: &StakeHistory,
        cached_vote_accounts: &CachedVoteAccounts<'_>,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        new_rate_activation_epoch: Option<Epoch>,
        delay_commission_updates: bool,
        commission_rate_in_basis_points: bool,
        adjust_delegations_for_rent: bool,
        ag_epoch_type: &AlpenglowEpochType,
        custom_commission_collector: bool,
        use_fixed_point_stake_math: bool,
    ) -> Option<InflationRewardWithCommission> {
        // curry closure to add the contextual stake_pubkey
        let reward_calc_tracer = reward_calc_tracer.as_ref().map(|outer| {
            // inner
            move |inner_event: &_| {
                outer(&RewardCalculationEvent::Staking(stake_pubkey, inner_event))
            }
        });

        let CachedVoteAccounts {
            snapshot_epoch_vote_accounts,
            rewarded_epoch_vote_accounts,
            distribution_epoch_vote_accounts,
        } = cached_vote_accounts;

        let vote_pubkey = stake_account.delegation().voter_pubkey;

        let current_lamports = stake_account.lamports();
        let minimum_lamports = self
            .rent_collector
            .rent
            .minimum_balance(stake_account.data_len());
        let stake = *stake_account.stake();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1095)
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
        (stake_rewards, partition_indices)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2770-2798)
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

**File:** programs/vote/src/vote_state/mod.rs (L1084-1121)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
```
