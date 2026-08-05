## Title
Stake accounts closed/withdrawn between epoch-reward calculation and partitioned distribution cause permanently burned rewards - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
The Beanstalk report describes a "snapshot vs. later state" mismatch: rewards are computed against a root/stake snapshot taken at one point in time (rain start), but paid out later based on the *current* balance, so if the balance goes to zero in between, the reward becomes permanently unclaimable. Agave's partitioned epoch-rewards machinery has the same structural pattern: `calculate_stake_rewards_and_commissions` computes each stake account's reward at the epoch boundary [1](#0-0) , but the actual credit only happens several blocks later in `build_updated_stake_reward`, which looks the account up again by pubkey in the *live* `stakes_cache_accounts` snapshot at distribution time [2](#0-1) . If that lookup fails, the reward is not credited to anyone — it is simply burned from the total capitalization.

### Finding Description
`calculate_stake_rewards_and_commissions` runs once, at the first block of the new epoch, and produces a `PartitionedStakeReward` for every stake account that had a delegation at that point [3](#0-2) . These entries are then hashed into partitions and paid out over the following `distribution_starting_block_height .. distribution_end_exclusive` blocks [4](#0-3) . That interval can span up to 10% of an epoch's slots per `get_reward_distribution_num_blocks` [5](#0-4) .

When a specific partition's turn comes, `store_stake_accounts_in_partition` re-fetches each stake account from the *current* stakes cache (not the snapshot used at calculation time) via `build_updated_stake_reward`:
```
let stake_account = stakes_cache_accounts
    .get(&partitioned_stake_reward.stake_pubkey)
    .ok_or(DistributionError::AccountNotFound)?
``` [2](#0-1) 

If the account is no longer present — e.g., a stake account that finished deactivating (as of the rewarded epoch) is withdrawn to zero lamports and closed by its owner before its partition is processed — the lookup fails with `DistributionError::AccountNotFound`, and `store_stake_accounts_in_partition`'s error branch treats the computed reward as burned rather than delivered to the staker:
```rust
Err(err) => {
    error!(...);
    stake_reward_lamports_burned += stake_reward_amount;
    block_reward_lamports_burned += block_reward_amount;
}
``` [6](#0-5) 

The code's own comment states this "should never" happen: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [7](#0-6)  That assumption is exactly the analog of the Beanstalk bug: Beanstalk also assumed the roots recorded at "rain start" would still correspond to a live, claimable account, but a withdrawal in between broke that invariant. Here, nothing in `distribute_partitioned_epoch_rewards`/`store_stake_accounts_in_partition` actually re-verifies that "further state mutation" (i.e., account closure via withdraw) is prevented; it merely assumes the stake program enforces it, and if that assumption is wrong for any edge case (e.g., a stake account that is *already fully deactivated as of the rewarded epoch* — since deactivated/closed accounts are not "active" and the stake program's own deactivation/close rules do not track "pending epoch-rewards distribution" state at all, as no `EpochRewardStatus`/`RewardInterval` check exists anywhere in the stake program), the reward computed for that staker is silently and permanently burned instead of credited to the rightful owner.

Just as in Beanstalk (where `handleRainAndSops` early-returns on `roots == 0` and orphans the SOP portion belonging to the withdrawn depositor, note by the reporter that the correct fix is to check for a *pending* rain/reward flag rather than the current balance), Agave's flow keys the final payout strictly off of whatever is currently in `stakes_cache_accounts`, with no separate bookkeeping tying the specific staker's pubkey/claim to the computed `PartitionedStakeReward` independent of the live account still existing.

### Impact Explanation
The affected staker's already-earned inflation and block reward for the rewarded epoch is converted into a straight capitalization decrease (`stake_reward_lamports_burned`) instead of being paid to them [8](#0-7) . This is a real, if narrow, "loss of rewards" for an unprivileged user acting entirely within normal protocol rules (deactivate stake, then withdraw once fully deactivated) — no malicious peer, validator, or trusted component is required. It matches the "fund theft/loss" category for unprivileged runtime/accounts issues.

### Likelihood Explanation
Likelihood is low-to-moderate and depends on precise timing: the staker's delegation must have already been fully deactivated as of the rewarded epoch (so it earns a final `DeactivatedStake` reward), and the staker must submit a `Withdraw` instruction that empties the account before their specific partition (which could be their very first eligible block, up to ~10% of the epoch later) is processed. This requires no special privilege, but does require the user to have timing knowledge of which block height their partition falls in (deterministic from the parent blockhash used in `hash_rewards_into_partitions`), which an attacker/self-harmed user could compute in advance. The severity to the individual is limited to their own final epoch's reward, not third-party funds, which somewhat limits blast radius relative to the Beanstalk case, but the underlying broken invariant (calculate now, look up state again later, treat "not found" as burn rather than escrow-to-claim) is directly structurally analogous.

### Recommendation
Do not resolve rewards purely by a fresh stakes-cache lookup at distribution time. Either (a) carry a copy/handle of the exact stake account state captured at calculation time forward to distribution and apply the reward to that captured state (paying out lamports to the withdraw destination or an escrow if the account no longer exists), or (b) explicitly disallow/queue `Withdraw`/close instructions against a stake account that still has an outstanding computed-but-undistributed reward (mirroring the `RewardInterval` check that already exists for other purposes) so `AccountNotFound` can genuinely never occur, matching the code's stated invariant.

### Proof of Concept
Conceptual repro (concrete integration test would need to be built against `runtime/src/bank/partitioned_epoch_rewards/*` test harness, similar to `test_distribute_with_increased_rent`):
1. Create a stake account, delegate it, then request deactivation such that it is fully deactivated as of `rewarded_epoch = current_epoch - 1`.
2. Advance to the epoch boundary; `calculate_stake_rewards_and_commissions` computes a nonzero `PartitionedStakeReward` for this pubkey (type `DeactivatedStake`) because it still had `stake_history` credits for part of the rewarded epoch.
3. Before this pubkey's partition height (`distribution_starting_block_height + partition_index`) is reached, submit a `Withdraw` instruction draining the stake account to 0 lamports (permitted since it is fully deactivated), closing/removing it from `stakes_cache_accounts`.
4. When the assigned partition block is processed, `build_updated_stake_reward` returns `Err(DistributionError::AccountNotFound)` [2](#0-1) , and `store_stake_accounts_in_partition` adds the reward to `stake_reward_lamports_burned`/`block_reward_lamports_burned` instead of crediting the staker [6](#0-5) .
5. Verify capitalization decreases by the burned amount and no account anywhere receives it — the staker's rightfully earned reward for the rewarded epoch is permanently gone.

Note: I was not able to locate any stake-program-side guard that ties `Withdraw`/account-closure eligibility to the bank's `EpochRewardStatus`/`RewardInterval` (searches for `RewardInterval`, `epoch_reward_status`, etc. inside stake-program code returned no matches), which is consistent with the distribution code's comment being an unverified assumption rather than an enforced invariant. Given index-size limits, I could not fully confirm the stake program's `Withdraw` instruction handler code directly (it wasn't retrievable in this search), so I cannot state with 100% certainty that no such guard exists elsewhere; a full Devin session with complete repo access would be needed to definitively confirm the exact deactivation/withdraw timing rules in the stake program and produce a runnable end-to-end test.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-791)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L803-820)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L137-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L399-406)
```rust
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L416-422)
```rust
        DistributionResults {
            stake_reward_lamports_minted,
            stake_reward_lamports_burned,
            block_reward_lamports_distributed,
            block_reward_lamports_burned,
            updated_stake_rewards,
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
