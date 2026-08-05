Found a real Agave analog in the same bug class: a value computed at time T (reward calculation) is applied against destination-account state fetched later at time T+Δ (distribution), and if that destination state has changed in a way that makes application fail, the funds are silently burned and — in the pre-`custom_commission_collector` legacy path — burned **without being counted** in the burned-lamports accounting, exactly mirroring the "destination state changes between initiation and finalization → funds get stuck/vanish uncounted" flaw from the report.

### Title
Vote-commission rewards silently disappear from capitalization accounting when the commission account is closed between reward calculation and distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`load_and_reward_commission_accounts` re-fetches each commission (vote) account "fresh" at distribution time and adds the previously-calculated `commission_lamports` to it [1](#0-0) . In the legacy (`!custom_commission_collector`) branch, the code assumes the commission account — always a vote account pre-SIMD-0232 — "should always exist" at distribution time, and if it doesn't, it simply logs a debug message and returns `None`, **without adding `*commission_lamports` to `total_non_incinerator_burned_lamports`** [2](#0-1) . This is exactly analogous to the reported bug class: an amount is computed against a destination that is checked/valid at calculation time, but the destination's real-world state (existence/eligibility) can change before the value is actually applied, and the code's error handling for that gap silently drops the value instead of accounting for it (or refunding it).

### Finding Description
The comment for this function explicitly states the intent: "This is the single point where commission account data is fetched, ensuring we always see the latest balances ... that happen between calculation and distribution" [3](#0-2) . This acknowledges that a time gap exists between when `commission_lamports` is calculated (during `redeem_delegation_rewards`) and when it's actually applied to the commission/vote account (during `load_and_reward_commission_accounts`), which is the reward-distribution phase run across many subsequent blocks after calculation [4](#0-3) .

In the legacy path, the guard comment says a vote account "cannot be closed unless the account hasn't voted for at least a full epoch," so `maybe_commission_account` "should always exist" [5](#0-4) . This is treated as an invariant, but it is not actually enforced by any explicit check in this function — it is only ever true if the vote-account close path in `vote_state::withdraw` (`programs/vote/src/vote_state/mod.rs`) correctly rejects closure whenever `epoch_credits` shows recent voting activity [6](#0-5) . If that invariant is ever violated for any reason (e.g. a future SIMD change, a different vote-account version/format not covered by that check, or a bug in `reject_active_vote_account_close`/`epoch_credits` computation), the `None` branch is taken and:

1. `*commission_lamports` is dropped from `distributed_lamports`/`accounts_with_rewards` (nothing is stored for this recipient), and
2. it is **not** added to `total_non_incinerator_burned_lamports` either.

Contrast this with every other failure branch in the same function — insufficient-funds overflow on `checked_add_lamports` and `collector_type_checked` errors both explicitly add the un-deliverable `commission_lamports` to `total_non_incinerator_burned_lamports` before returning `None` [7](#0-6) . Only the "commission account missing" branch fails to do this bookkeeping. `distributed_lamports` and `burned_lamports` returned from this function feed directly into `capitalization` adjustments and reward-history reporting (`distribute_epoch_rewards_in_partition` adds `stake_reward_lamports_minted` to `capitalization` and subtracts `block_reward_lamports_burned`, using these very totals) [8](#0-7) . If the "missing account" branch is ever reachable, the lamports vanish from the ledger's accounting entirely: capitalization is not reduced by the burned amount and the reward is not credited anywhere, an unaccounted lamport disappearance rather than a properly tracked burn.

### Impact Explanation
If this branch is reachable, it produces a state inconsistency: total lamports minted for inflation are computed and expected to be delivered, but for the missing-account case the burn is not deducted from capitalization tracking, while lamports were still never delivered to any account. This is a validator-consensus-relevant accounting bug (all validators execute this deterministically so it wouldn't itself cause divergence, but it represents a real "silent fund loss/uncounted burn" bug matching the reported bug class) rather than fund theft by an attacker. Given the code explicitly documents the "should always exist" assumption as its only safety net rather than an enforced invariant, the actual severity depends entirely on whether that vote-account-liveness invariant can be violated — which I was not able to fully verify from local code alone given the number of code paths that can create/close/reassign vote-account state (rent collection, SIMD-0392 delegation adjustments, etc.).

### Likelihood Explanation
Low-to-medium: this path only fires when `custom_commission_collector` (SIMD-0232) is inactive and only when a vote account that received a commission-reward calculation is subsequently absent by the time distribution actually runs — several blocks/epoch-partitions later. The existing `reject_active_vote_account_close` check in `withdraw()` is the only guard preventing this, and it depends on `epoch_credits` bookkeeping being perfectly correct across all code paths, including under SIMD-0392 rent adjustments which can force `deactivation_epoch` changes at distribution time itself [9](#0-8) .

### Recommendation
In the `!custom_commission_collector` branch, treat a missing commission account the same as the other failure branches: add `*commission_lamports` to `total_non_incinerator_burned_lamports` before returning `None`, so capitalization accounting always matches actual lamport disposition regardless of whether the "vote account cannot be closed" invariant continues to hold in all future code paths.

### Proof of Concept
Not independently exploitable/reproducible from local code alone without a live path that closes a vote account between the calculation and distribution phases of partitioned epoch rewards; I could not confirm within the available context whether such a path currently exists (this would require deeper analysis of every vote-account-closing / reassignment code path across `programs/vote` and `runtime/src/bank/partitioned_epoch_rewards`). The concrete, verifiable defect is the asymmetric/missing burn-accounting in the "commission account missing at distribution time" branch itself [10](#0-9) , which is a real deviation from the pattern used by every sibling error branch in the same function.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1097-1129)
```rust
    /// Load each planned commission account from the store and apply its
    /// reward. This is the single point where commission account data is
    /// fetched, ensuring we always see the latest balances — including any
    /// intervening account mutations (e.g. VAT burns in `update_epoch_stakes`)
    /// that happen between calculation and distribution.
    fn load_and_reward_commission_accounts(
        &self,
        reward_commissions: &RewardCommissions,
        thread_pool: &ThreadPool,
    ) -> RewardCommissionAccounts {
        let reserved_account_keys = &self.reserved_account_keys;
        let rent = &self.rent_collector().rent;
        let feature_snapshot = self.feature_set.snapshot();
        let relax_post_exec_min_balance_check = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let total_non_incinerator_burned_lamports = AtomicU64::new(0);
        let total_incinerator_lamports = AtomicU64::new(0);

        let accounts_with_rewards: Vec<_> = thread_pool.install(|| {
            reward_commissions
                .par_iter()
                .filter_map(
                    |(
                        commission_pubkey,
                        RewardCommission {
                            commission_bps,
                            commission_lamports,
                            burned_lamports,
                            is_vote_account,
                        },
                    )| {
                        let maybe_commission_account =
                            self.get_account_with_fixed_root_no_cache(commission_pubkey);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1130-1149)
```rust
                        let mut commission_account = if custom_commission_collector {
                            // If the account doesn't exist, the vote commission
                            // may be enough lamports to cover rent-exemption
                            // and properly create the commission account.
                            maybe_commission_account.unwrap_or_default()
                        } else {
                            // Before SIMD-0232, commission accounts were always
                            // vote accounts, which cannot be closed unless the
                            // account hasn't voted for at least a full epoch.
                            // This means that `maybe_commission_account` should
                            // always exist.
                            let Some(commission_account) = maybe_commission_account else {
                                debug!(
                                    "commission account {commission_pubkey} missing at \
                                     distribution time"
                                );
                                return None;
                            };
                            commission_account
                        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1154-1186)
```rust
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
                        if !is_vote_account {
                            match Self::collector_type_checked(
                                commission_pubkey,
                                pre_lamports,
                                &commission_account,
                                reserved_account_keys,
                                rent,
                                relax_post_exec_min_balance_check,
                            ) {
                                Ok(ExternalCollectorType::SystemAccount) => {}
                                Ok(ExternalCollectorType::Incinerator) => {
                                    total_incinerator_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                }
                                Err(err) => {
                                    debug!(
                                        "reward redemption failed for {commission_pubkey} due to \
                                         commission account error: {err:?}"
                                    );
                                    total_non_incinerator_burned_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                    return None;
                                }
                            }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L49-76)
```rust
/// Adjusts stake delegation based on Rent sysvar parameters.
///
/// As part of SIMD-0392, if Rent is ever increased, we need to make sure that
/// lamports are not double-counted for the rent-exempt minimum and the stake
/// delegation. This function adjusts the delegation in a Stake if needed, right
/// at distribution time.
fn adjust_delegation_for_rent(
    delegation: &mut Delegation,
    rewarded_epoch: Epoch,
    new_delegation_with_rewards: u64,
    lamports_with_rewards: u64,
    minimum_lamports: u64,
) {
    let new_delegation = std::cmp::min(
        new_delegation_with_rewards,
        lamports_with_rewards.saturating_sub(minimum_lamports),
    );

    if new_delegation != delegation.stake {
        delegation.stake = new_delegation;
        // Deactivate stake if needed. This deactivation is immediate,
        // unlike a requested deactivation which happens at the next epoch
        // boundary
        if new_delegation == 0 {
            delegation.deactivation_epoch = rewarded_epoch;
        }
    }
}
```

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L175-204)
```rust
    fn distribute_epoch_rewards_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) {
        let pre_capitalization = self.capitalization();
        let (
            DistributionResults {
                stake_reward_lamports_minted,
                stake_reward_lamports_burned,
                block_reward_lamports_distributed,
                block_reward_lamports_burned,
                updated_stake_rewards,
            },
            store_stake_accounts_us,
        ) = measure_us!(self.store_stake_accounts_in_partition(partition_rewards, partition_index));

        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
```

**File:** programs/vote/src/vote_state/mod.rs (L1093-1111)
```rust

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
```
