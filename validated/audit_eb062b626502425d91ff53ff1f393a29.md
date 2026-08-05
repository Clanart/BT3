### Title
Deposit-timing arbitrage in partitioned stake reward distribution: post-calculation lamport transfers change rent-adjustment/destake outcome - (File: `runtime/src/inflation_rewards/mod.rs`, `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
The external report's broken invariant is: a value used to settle balances is computed once (an average/snapshot) at an epoch boundary, but real state can diverge from it before it is actually applied, letting an actor act on stale/soon-to-be-stale information to capture value that other participants cannot access. The Agave analog is in partitioned epoch-rewards processing: `redeem_stake_rewards` decides, at reward-calculation time, whether a stake delegation "needs adjustment" (destake / cap) based on `current_lamports` observed *at calculation time*, via `delegation_may_need_adjustment` [1](#0-0) . The actual state mutation and lamport application happen later, at distribution time, in `build_updated_stake_reward` / `store_stake_accounts_in_partition`, which is split across many blocks after the epoch boundary [2](#0-1) . A stake owner can transfer lamports into the stake account in the window between calculation and their partition's distribution block to influence the outcome that was already computed against outdated balances — a direct structural analog to "using an average/snapshot value for settlement while the underlying state can move before it takes effect."

### Finding Description
`redeem_stake_rewards` computes whether the stake delegation must be adjusted for rent using `current_lamports` (the balance observed during the single "calculation" pass at the epoch boundary) and `minimum_lamports`: [1](#0-0) 

This decision (`needs_adjustment`) determines whether the account is a candidate for de-staking/capping (`RewardType::DeactivatedStake`) versus a normal stake update. However, the actual reward + lamport application does not happen at calculation time — it happens later during `store_stake_accounts_in_partition`/`build_updated_stake_reward`, potentially many blocks after calculation, since rewards are paid out in partitions spread across the epoch (`distribution_starting_block_height`, `partition_indices`) [3](#0-2) . At that later point, `build_updated_stake_reward` re-reads the live stake account from `stakes_cache_accounts` and calls `adjust_delegation_for_rent` using the account's lamports *at distribution time*, not at calculation time: [4](#0-3) 

The codebase itself documents this gap explicitly: comments in `delegation_may_need_adjustment` and the redeem-delegation-rewards path state "the actual adjustment happens at distribution, to account for any lamports credited to the account during partitioned epoch rewards, before the distribution has occurred" [5](#0-4) , and `redeem_delegation_rewards` notes "delegation for stake ... may be adjusted at distribution, unless lamports are transferred before distribution block" [6](#0-5) . This is confirmed by an existing unit test, `test_delegation_adjustment_at_distribution`, which explicitly transfers additional lamports into the stake account *after* calculation but *before* distribution, and shows the outcome (delegation update vs. destake) changes as a result: [7](#0-6) 

The core parallel to the PnL report: the settlement decision is derived from a value frozen at one point in time (calculation-time balance, analogous to the averaged/snapshotted PnL), while the entity being settled (the stake account) can change state before the settlement is actually applied (distribution), and other stake accounts in earlier/later partitions do not get this same window, or get it under different market/rent conditions. Existing guards (`snapshot_epoch_vote_accounts` used to prevent "last-minute commission rugs" for vote-account commission) explicitly address a similar timing gap for commissions [8](#0-7) , but no equivalent snapshot/freeze exists for the stake account's lamport balance used in the rent-adjustment decision — the guard was applied to one input (vote commission) but not to the other (stake lamports), leaving the asymmetry the report describes intact for this specific code path.

### Impact Explanation
This does not enable direct fund theft from the protocol/vault (the reward pool total is fixed by `PointValue`/`total_rewards`), so the impact is analogous to the "Low" impact rated in the original report: it creates an uneven/unfair outcome between stake accounts around the epoch boundary — some accounts can strategically time lamport transfers to avoid being flagged for delegation adjustment/destaking or to influence their effective post-reward delegation, while others (whose partition is processed earlier or who don't game the timing) are settled under stale assumptions. It does not cause consensus halt or non-deterministic behavior since all validators replay the same transactions deterministically, but it is an economic-fairness bug in the reward/rent-adjustment design, matching the report's "some users benefit more than others" pattern.

### Likelihood Explanation
Likelihood is bounded by the fact that `adjust_delegations_for_rent` is gated behind the `relax_post_exec_min_balance_check` feature (only active when that feature is enabled), and requires the attacker to know exactly which partition/block their stake account will be processed in and to send a transfer transaction that lands before that specific block. This is knowable in principle because partition indices are derived deterministically via `hash_rewards_into_partitions` from the parent blockhash, and stake owners can compute their own partition ahead of time and simply time a transfer transaction to land before their scheduled distribution block [9](#0-8) . This is a normal unprivileged system transfer available to any account holder — no malicious peer/validator/leaked-key assumption is required.

### Recommendation
Freeze the lamport balance (and/or the rent-adjustment decision) used for `delegation_may_need_adjustment` at calculation time and carry that decision through to distribution unchanged, rather than re-evaluating destake/adjustment eligibility against the live balance at the later distribution block. Alternatively, apply the same "snapshot a full epoch ahead" pattern already used for `snapshot_epoch_vote_accounts` (to prevent last-minute commission changes) to the stake lamport balance used for rent-adjustment decisions, closing the timing gap between calculation and distribution.

### Proof of Concept
The existing test `test_delegation_adjustment_at_distribution` in the codebase is itself a working PoC of the divergence: it sets up a stake reward that would normally destake the account (small reward, balance below new minimum), then transfers `1_000_000_000` lamports into the stake account after calculation but before `distribute_epoch_rewards_in_partition` runs, and asserts the delegation is instead updated (not destaked) as a result of the transfer landing in the calculation-to-distribution window [7](#0-6) . A stake owner can reproduce this in production by identifying which partition/block their stake account is due for and submitting an ordinary system transfer to their own stake account immediately before that block is processed.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L146-164)
```rust
    let staker_rewards = maybe_rewards.map(|x| x.0).unwrap_or(0);
    if adjust_delegations_for_rent {
        let new_delegation_with_rewards = stake.delegation.stake.saturating_add(staker_rewards);
        let needs_adjustment = delegation_may_need_adjustment(
            stake.delegation.stake,
            new_delegation_with_rewards,
            current_lamports.saturating_add(staker_rewards),
            minimum_lamports,
            status,
        );
        // If `maybe_rewards.is_some()`, need to drive forward credits, even
        // if rewards are zero
        if needs_adjustment || maybe_rewards.is_some() {
            stake.delegation.stake = new_delegation_with_rewards;
            let voter_rewards = maybe_rewards.map(|x| x.1).unwrap_or(0);
            Some((staker_rewards, voter_rewards))
        } else {
            None
        }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L299-325)
```rust
        let stake_at_distribution_epoch = delegation_effective_stake(
            &new_stake.delegation,
            distribution_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        let reward_type = if stake_at_distribution_epoch == 0 {
            RewardType::DeactivatedStake
        } else {
            RewardType::Staking
        };
        Ok(StakeReward {
            stake_pubkey: partitioned_stake_reward.stake_pubkey,
            stake_reward_info: StakeRewardInfo {
                reward_type,
                lamports: i64::try_from(
                    partitioned_stake_reward.inflation.stake_reward
                        + partitioned_stake_reward.block_reward,
                )
                .unwrap(),
                post_balance: account.lamports(),
                commission_bps: partitioned_stake_reward.inflation.commission_bps,
            },
            stake_account: account,
        })
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-365)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1246-1292)
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

        // Distribute rewards
        let pre_cap = bank.capitalization();
        bank.distribute_epoch_rewards_in_partition(&partitioned_rewards, 0);
        let post_cap = bank.capitalization();
        let post_epoch_rewards_account = bank.get_account(&sysvar::epoch_rewards::id()).unwrap();

        // Assert that epoch rewards sysvar lamports balance does not change
        assert_eq!(post_epoch_rewards_account.lamports(), expected_balance);

        let epoch_rewards: sysvar::epoch_rewards::EpochRewards =
            from_account(&post_epoch_rewards_account).unwrap();
        assert_eq!(epoch_rewards.total_rewards, total_rewards);
        assert_eq!(epoch_rewards.distributed_rewards, rewards_to_distribute,);

        // Assert that the bank total capital changed by the amount of rewards
        // distributed
        assert_eq!(pre_cap + rewards_to_distribute, post_cap);

        // Check that delegation just gets rewards
        let post_account = bank.get_account(&stake_pubkey).unwrap();
        let post_stake_state: StakeStateV2 = post_account.state().unwrap();
        let pre_stake_state: StakeStateV2 = stake_account.state().unwrap();
        assert_eq!(
            post_stake_state.delegation().unwrap().stake,
            pre_stake_state.delegation().unwrap().stake + reward_lamports
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L663-673)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1089-1093)
```rust
        let partition_indices = hash_rewards_into_partitions(
            &stake_rewards,
            &epoch_rewards_sysvar.parent_blockhash,
            epoch_rewards_sysvar.num_partitions as usize,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```
