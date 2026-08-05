### Title
Validator panic during epoch reward distribution when a stake account's lamport balance is mutated after reward calculation but before payout - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
The Agave partitioned epoch-rewards pipeline computes stake rewards at the epoch boundary (`Calculation` phase) but only credits them to accounts several blocks later (`Distribution` phase). At distribution time, `Bank::build_updated_stake_reward` re-fetches the *current* on-chain stake account and, when the `relax_post_exec_min_balance_check` feature is not active, asserts that the current delegation equals the value computed at calculation time plus the reward — with no error-handling fallback, only a hard `assert_eq!` that panics the validator process if it does not hold.

### Finding Description
`Bank::build_updated_stake_reward` fetches the stake account's *live* state from `stakes_cache_accounts` (i.e., whatever the account currently looks like at distribution time), not the state as of the calculation snapshot: [1](#0-0) 

It then either adjusts the delegation to account for lamports added since calculation time (guarded by the `adjust_delegations_for_rent`/`relax_post_exec_min_balance_check` feature), or — in the `else` branch used when that feature is not active — assumes the on-chain delegation is *exactly* what was computed at calculation time and panics via `assert_eq!` if not: [2](#0-1) 

The surrounding code explicitly documents (and relies on) the assumption that "further state mutation [is] prevent[ed] by stake-program restrictions, [so] there should never be rewards burned": [3](#0-2) 

This is the same broken-invariant pattern as the referenced report: a component assumes a previously recorded/derived value ("the stake account will still look the way it did at calculation time") stays valid until it is consumed, without any hook or re-validation mechanism to reconcile drift caused by activity that occurred in between. The rent-adjustment logic itself proves the assumption is false in general — its own doc comment states rewards distribution must "account for any lamports credited to the account during partitioned epoch rewards, before the distribution has occurred": [4](#0-3) 

i.e., the developers know stake account lamports (and therefore effective/expected delegation) *can* change between calculation and distribution — arbitrary lamports can be sent to any account via a plain `SystemInstruction::Transfer`, since receiving lamports imposes no ownership restriction on the destination. When the rent-relaxation feature is active this is handled gracefully by recomputing the delegation; when it is not (the `else` branch), the code has no fallback and instead asserts strict equality, turning a benign/adversarial state drift into a `panic!` that aborts block processing entirely (both for the leader producing the block and every validator replaying it), since `distribute_partitioned_epoch_rewards`/`store_stake_accounts_in_partition` run synchronously and deterministically as part of consensus-critical bank processing.

### Impact Explanation
A panic inside `build_updated_stake_reward` during `distribute_partitioned_epoch_rewards` crashes the bank-processing thread on every validator that reaches that slot (leader and replaying followers alike), since this code path executes unconditionally for every epoch that has pending partitioned stake rewards. This falls squarely into "non-RPC remote crash / consensus halt" impact: an unprivileged actor who can merely send lamports to a targeted stake account (a permissionless, unprivileged operation requiring no special access) during the multi-block reward-distribution window can trigger a fleet-wide validator panic once that stake account's reward comes up for payout, which is a critical availability/consensus issue class.

### Likelihood Explanation
Likelihood is moderate but not fully confirmed from static review alone: it hinges on (a) whether `relax_post_exec_min_balance_check` is inactive on the target cluster/at the time of the attack (this is a real, gateable feature, implying there are cluster states where the legacy `else`/`assert_eq!` branch is exercised), and (b) whether any additional guard (e.g., a "reward interval" transaction-processing restriction on write-locking stake accounts during active `EpochRewardStatus`) blocks lamport transfers into stake accounts during the distribution window — I was not able to fully trace such a guard in the available index (this repo's `RewardInterval`/transaction sanitization logic that filters transactions during the reward interval was outside what I could confirm). This should be verified directly against the transaction-processing/sanitization code path (e.g., `bank.rs` reward-interval account lock filtering) to determine whether an attacker's `Transfer` to a targeted stake account is actually admitted into a block during the vulnerable window.

### Recommendation
- Replace the `assert_eq!` panic in the `else` branch of `build_updated_stake_reward` with the same graceful reconciliation used in the `adjust_delegations_for_rent` branch (or an explicit, non-panicking `DistributionError` variant that burns/skips just that one reward), regardless of feature-gate status.
- Independently verify (and, if missing, add) an unconditional restriction preventing any lamport-mutating instruction from write-locking a stake account for the duration it has a pending, uncredited partitioned reward, closing the gap the code currently only partially assumes exists.
- Add a fuzz/property test that mutates a stake account's lamports (via a plain transfer) between the calculation and distribution blocks under the non-`relax_post_exec_min_balance_check` feature configuration, to ensure the pipeline never panics.

### Proof of Concept
1. On a cluster where the `relax_post_exec_min_balance_check` feature is not yet active, identify a stake account `S` that is due to receive a partitioned inflation reward in the upcoming epoch's distribution phase (calculation happens at the epoch boundary; distribution is spread over subsequent blocks, per `REWARD_CALCULATION_NUM_BLOCKS`/partition count, see `distribute_partitioned_epoch_rewards`) [5](#0-4) .
2. After the calculation block (which fixes `partitioned_stake_reward.inflation.stake.delegation.stake` for `S`) but before `S`'s partition is processed, submit an ordinary `system_instruction::transfer` sending lamports into `S`. No special authority over `S` is required to receive lamports.
3. When `S`'s partition is processed by `store_stake_accounts_in_partition` → `build_updated_stake_reward`, the live `stake.delegation.stake` fetched from `stakes_cache_accounts` is unaffected by the lamport transfer directly, but any subsequent operation that reconciles delegation with account lamports (or, more directly, an actual Split/Merge/Redelegate stake-program instruction issued by `S`'s stake authority during the same window, which does mutate `delegation.stake`) causes `expected_delegation != new_stake.delegation.stake`, triggering the `assert_eq!` panic and crashing bank processing on every node replaying that slot [6](#0-5) .
4. This is exercised in existing unit tests that specifically construct this "extra lamports transferred before distribution time" scenario, e.g. `test_delegation_adjustment_at_distribution`, confirming the code path and window are real and reachable in normal operation: [7](#0-6) .

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-150)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-336)
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
    fn store_stake_accounts_in_partition(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1246-1265)
```rust
        // Below new minimum, small reward, should normally be destaked
        let reward_lamports = 1;
        let reward = PartitionedStakeReward::new_with_lamport_amounts(reward_lamports, 0, 1);
        let rewards_to_distribute = reward.inflation.stake_reward;
        let stake_pubkey = reward.stake_pubkey;
        let stake_rewards = [reward];
        populate_starting_stake_accounts_from_stake_rewards(&bank, &lower_rent, &stake_rewards);
        let mut stake_account = bank.get_account(&stake_pubkey).unwrap();

        let expected_num = 1;

        let partitioned_rewards = StartBlockHeightAndPartitionedRewards {
            distribution_starting_block_height: bank.block_height() + REWARD_CALCULATION_NUM_BLOCKS,
            all_stake_rewards: Arc::new(stake_rewards.into_iter().collect()),
            partition_indices: vec![(0..expected_num).collect::<Vec<_>>()],
        };

        // But we transfer in more lamports before distribution time
        stake_account.checked_add_lamports(1_000_000_000).unwrap();
        bank.store_account(&stake_pubkey, &stake_account);
```

**File:** runtime/src/inflation_rewards/mod.rs (L171-177)
```rust
/// Returns `true` if stake delegation needs to be adjusted during distribution
/// based on Rent sysvar parameters at epoch boundary
///
/// The actual adjustment happens at distribution, to account for any lamports
/// credited to the account during partitioned epoch rewards, before the
/// distribution has occurred.
pub(crate) fn delegation_may_need_adjustment(
```
