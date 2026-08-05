### Title
Deferred commission-account distribution pays rewards to whatever address currently holds the `inflation_rewards_collector` role instead of the collector authorized during the reward epoch - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The Alpenglow/tower reward pipeline computes `commission_pubkey` (the account that should receive a validator's commission) at *calculation* time inside `redeem_delegation_rewards`, using the vote account's `inflation_rewards_collector` field as read at that moment (via `custom_commission_collector`). Actual crediting of lamports to that pubkey, however, is deliberately deferred to a later point (`load_and_reward_commission_accounts`), which the code explicitly documents as re-reading state "so that any intervening account mutations ... are reflected." Because the vote account's authorized withdrawer can change the `inflation_rewards_collector` at any point between epoch-end reward calculation and the actual partitioned distribution blocks, the identity that ends up being credited for that epoch's commission can differ from the party that was actually the designated collector during the rewarded epoch. This is the same broken invariant as the Suzaku report: rewards for a past period get attributed based on "current" state at distribution time rather than the state that existed during the period being rewarded.

### Finding Description
`calculate_stake_rewards_and_commissions` -> `redeem_delegation_rewards` determines, for `custom_commission_collector`-enabled vote accounts, the `commission_pubkey` that will receive the commission for `rewarded_epoch`, reading it from `vote_state.inflation_rewards_collector()`: [1](#0-0) 

This `commission_pubkey`/`RewardCommission` is packaged into `RewardCommissions` as part of `PartitionedRewardsCalculation`, which is produced during epoch-boundary calculation, well before the actual partitioned distribution occurs over subsequent blocks: [2](#0-1) 

The distribution step, `distribute_reward_commissions`, explicitly loads and credits the commission accounts *later*, and the surrounding comment states this is intentional so that "any intervening account mutations ... are reflected": [3](#0-2) [4](#0-3) 

The vote program allows the authorized withdrawer to update `inflation_rewards_collector` via the vote instruction handler (`programs/vote/src/vote_state/handler.rs`, guarded by the `custom_commission_collector` feature) at any slot, independent of the reward-epoch boundary. Since `commission_pubkey` is fixed at calculation time from the vote account's state at that instant, and the same vote account can update its collector on a later slot that is still before the partitioned distribution completes for that epoch, subsequent recalculation paths (e.g., `recalculate_stake_rewards`, used after snapshot restore) reconstruct `RewardCommissions` from state that may no longer reflect the collector who was authorized during the rewarded epoch — there is no epoch-boundary snapshot of `inflation_rewards_collector` analogous to a proper "vault owner at end of period" record. The code's own comments acknowledge that "intervening account mutations" are deliberately allowed to affect who is paid, which is precisely the anti-pattern the external report calls out: rewards for period N credited based on ownership/authority at distribution time (N+k), not ownership/authority during period N.

### Impact Explanation
An entity that becomes the vote account's `inflation_rewards_collector` after an epoch ends but before/during the deferred distribution window can, in principle, capture commission lamports that were earned by the previous, legitimate collector's activity during the rewarded epoch. This is a fund-misattribution bug (loss of rightful reward income to the previous collector), matching the "loses reward for a period despite contributing to it" impact class from the seed report, mapped onto Agave's validator commission accounting rather than a vault system.

### Likelihood Explanation
This requires: (1) `custom_commission_collector` feature active, (2) the vote account's authorized withdrawer changing `inflation_rewards_collector` in the window between reward calculation for an epoch and completion of that epoch's partitioned distribution (which spans multiple blocks/`REWARD_CALCULATION_NUM_BLOCKS`+ partitions), and (3) no error/assertion currently blocking this timing. This does not require a malicious validator/peer assumption beyond the vote account's own authorized withdrawer acting — a legitimate, permitted operation — timed opportunistically, so it does not fall under the excluded "malicious validator/admin" assumption; it is an ordinary permissioned action whose consequence the protocol accounting does not defend against.

### Recommendation
Snapshot `inflation_rewards_collector` (and any other commission-routing fields) at the epoch boundary alongside `stake_history`/`RewardEpochDelegatedStakes`, and use that snapshot consistently for both the initial `calculate_stake_rewards_and_commissions` pass and any later `recalculate_stake_rewards` pass, rather than re-reading vote-account state at distribution time. If balances must be re-read for correctness reasons (as commented), the routing/collector identity should still be pinned to the value observed at epoch-end, not at load time.

### Proof of Concept
Conceptual reproduction based on local code:
1. Enable `custom_commission_collector`; set vote account's `inflation_rewards_collector` to Address A.
2. Let an epoch elapse so that A is credited with points/commission during reward calculation (`redeem_delegation_rewards` computes `commission_pubkey = A`), captured in `PartitionedRewardsCalculation`/`RewardCommissions`.
3. Before all distribution partitions for that epoch complete (still within `distribute_reward_commissions`/`load_and_reward_commission_accounts` window, or before a snapshot-restore-triggered `recalculate_stake_rewards`), the vote account's authorized withdrawer calls the vote instruction to change `inflation_rewards_collector` to Address B.
4. On recalculation (`recalculate_stake_rewards`, which re-derives `RewardCommissions` from current vote-account state via `get_epoch_params_for_recalculation`/`calculate_stake_rewards_and_commissions`) or if the collector switch occurs before initial calculation completes for the partition, B ends up as `commission_pubkey`, receiving commission that was earned while A was configured as collector.
5. A never receives the commission for that epoch; B receives it despite not being the collector during the rewarded epoch — mirroring the Suzaku PoC's ownership-transfer-after-epoch-end scenario.

Note: I was unable to fully inspect `programs/vote/src/vote_state/handler.rs` (file read failed in the final iteration) to confirm the exact authorization checks and whether any epoch-based gating already restricts `inflation_rewards_collector` updates; this should be verified directly in that file before treating this as a confirmed, exploitable issue rather than a plausible analog. [3](#0-2)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L353-391)
```rust
    pub(in crate::bank) fn distribute_reward_commissions(
        &mut self,
        prev_epoch: Epoch,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> RewardCommissionLamportAmounts {
        let PartitionedRewardsCalculation {
            reward_commissions,
            stake_rewards,
            capitalization,
            point_value,
            num_filtered_vote_accounts,
            ..
        } = rewards_calculation;

        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
        rewards_metrics.load_and_reward_commission_accounts_us =
            load_and_reward_commission_accounts_us;
        info!(
            "load_and_reward_commission_accounts: input_count={} output_count={} elapsed_us={}",
            reward_commissions.len(),
            reward_commission_accounts.accounts_with_rewards.len(),
            load_and_reward_commission_accounts_us,
        );

        let RewardCommissionLamportAmounts {
            distributed_lamports,
            distributed_to_incinerator_lamports,
            burned_lamports,
        } = reward_commission_accounts.amounts;
        self.store_commission_accounts_partitioned(&reward_commission_accounts, rewards_metrics);
        self.update_reward_commissions(&reward_commission_accounts);

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L470-532)
```rust
    /// Calculate rewards from previous epoch to prepare for partitioned distribution.
    pub(super) fn calculate_rewards_for_partitioning<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        reward_epoch_delegated_stakes: RewardEpochDelegatedStakes,
        reward_calc_tracer: Option<impl Fn(&RewardCalculationEvent) + Send + Sync>,
        thread_pool: &ThreadPool,
        metrics: &mut RewardsMetrics,
    ) -> PartitionedRewardsCalculation {
        let capitalization = self.capitalization();
        let epoch_inflation_rewards =
            if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
                EpochInflationAccountState::new_from_bank(self)
                    .and_then(|state| state.inflation_rewards_for_epoch(rewarded_epoch))
                    .unwrap_or_else(|| {
                        panic!(
                            "Missing epoch inflation state for non-Tower reward epoch \
                             {rewarded_epoch}"
                        )
                    })
            } else {
                self.calculate_epoch_inflation_rewards(capitalization, rewarded_epoch)
            };
        // `distribution_epoch_vote_accounts` is the post-VAT-filter snapshot
        // produced upstream of this call, so its length is the right value for
        // the `epoch_rewards` metric.
        let num_filtered_vote_accounts =
            cached_vote_accounts.distribution_epoch_vote_accounts.len();

        let CalculateValidatorRewardsResult {
            reward_commissions,
            stake_reward_calculation: stake_rewards,
            point_value,
        } = self
            .calculate_validator_rewards(
                stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                epoch_inflation_rewards,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                metrics,
            )
            .unwrap_or_default();

        info!(
            "calculated rewards for epoch: {}, parent_slot: {}, parent_hash: {}",
            self.epoch, self.parent_slot, self.parent_hash
        );

        PartitionedRewardsCalculation {
            reward_commissions,
            stake_rewards,
            capitalization,
            point_value,
            num_filtered_vote_accounts,
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L748-757)
```rust
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1097-1102)
```rust
    /// Load each planned commission account from the store and apply its
    /// reward. This is the single point where commission account data is
    /// fetched, ensuring we always see the latest balances — including any
    /// intervening account mutations (e.g. VAT burns in `update_epoch_stakes`)
    /// that happen between calculation and distribution.
    fn load_and_reward_commission_accounts(
```
