### Title
`calculate_block_reward` clamps per-staker block-reward payout instead of erroring, silently under-paying stakers when `total_active_stake` is inconsistent with `pending_delegator_rewards` accounting - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report describes `ConvexMasterChef::safeRewardTransfer`, which caps a reward payout to whatever balance is actually available instead of reverting, silently shortchanging the recipient when the contract is undersupplied. `calculate_block_reward` in Agave implements the analogous pattern for SIMD-0123 block-revenue-sharing rewards: it computes a staker's share of `pending_delegator_rewards` proportional to `stake / total_active_stake`, and if that computed value exceeds `pending_delegator_rewards` it silently truncates (`.min(pending_delegator_rewards)`) rather than treating the mismatch as an error to be reconciled.

### Finding Description
`calculate_block_reward` computes each stake delegation's share of a vote account's `pending_delegator_rewards`: [1](#0-0) 

The comment directly preceding the clamp acknowledges the invariant can break: "During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`... We can also have individual rewards look greater than the pending rewards. This is harmless in practice, but we clamp it just to be safe" [2](#0-1) .

This mirrors `safeRewardTransfer` exactly: the function does not assert/error when the calculated amount would exceed the "available" pool (`pending_delegator_rewards`); it silently caps it. Unlike a genuine balance-based transfer (where clamping to the real balance is at least self-consistent), here the clamp is against a *notional* value (`pending_delegator_rewards`), and the actual decrement/redemption of that pool from the vote account is handled separately (via reward-commission accounting in `distribute_reward_commissions`/`load_and_reward_commission_accounts`) rather than being derived from the sum of per-staker block rewards actually paid out. There is no cross-check anywhere in `calculate_stake_rewards_and_commissions` or `distribute_reward_commissions` asserting that the sum of `block_reward` values computed for all delegations to a given vote account is `<=` that vote account's `pending_delegator_rewards`, nor is `total_active_stake` guaranteed to equal the sum of `delegation_effective_stake()` across all of that vote account's delegations at the time of calculation [3](#0-2) .

Because `calculate_block_reward` is invoked independently per stake delegation inside a parallel iterator, and each call only clamps its own individual result against the vote account's `pending_delegator_rewards` (not against a running total already allocated to other stakers of the same vote account), an inconsistency between `total_active_stake` (taken from `RewardEpochDelegatedStakes`) and the actual stake distribution used elsewhere in the calculation path can cause the sum of per-staker rewards to diverge from `pending_delegator_rewards` — either under-distributing (silently, via the clamp) or, in principle, if some other staker is over-credited relative to their true share, causing later/other stakers to receive less than the formula would have given them had the pool been correctly reduced as it was consumed.

### Impact Explanation
If `total_active_stake` and per-delegation effective stake become inconsistent with each other (the code's own comment states this is possible "during recalculation"), some delegators receive less than the SIMD-0123-mandated proportional share of block revenue, with no error, no burn accounting entry, and no on-chain signal that a shortfall occurred — the reward is simply computed smaller than it should be. This is a fund-loss condition for affected stakers (silent under-payment), analogous to the `safeRewardTransfer` finding, though here it is bounded to the block-revenue-sharing reward computation rather than an ERC20-style token balance.

### Likelihood Explanation
The clamp path is explicitly documented as reachable "during recalculation" (i.e., bank restart / snapshot-restore recalculation of rewards), a real, non-malicious code path that already exists in this codebase (see `get_epoch_params_for_recalculation`) [4](#0-3) . Because it requires only ordinary validator restart/recalculation behavior — not a malicious peer or trusted actor — it fits the "unprivileged … runtime/accounts" impact category. However, the comment's own assessment ("harmless in practice") suggests the Agave maintainers believe the discrepancy is bounded/negligible in realistic conditions; I was not able to fully verify from local code alone whether the divergence between `total_active_stake` and effective per-delegation stake sums can be large enough to produce a materially different (as opposed to rounding-level) shortfall.

### Recommendation
Either (a) assert/log an error metric whenever the pre-clamp computed reward exceeds `pending_delegator_rewards` so operators can detect drift instead of silently truncating, or (b) track and subtract already-allocated block reward from `pending_delegator_rewards` as each delegation is processed within the same vote account so that the clamp reflects the true remaining pool rather than a static snapshot value, ensuring the sum of individual payouts can never exceed the pool without visibility into the shortfall.

### Proof of Concept
Not independently reproducible from static analysis alone; the code's own inline comment concedes the exact scenario ("if stake account has already received rewards, it's possible to have `stake > total_active_stake`... individual rewards look greater than the pending rewards") [2](#0-1) , and unit tests in the same file exercise the clamp behavior directly (e.g. `test_calculate_block_reward_specific`, which asserts `get_block_reward_for_test(2, 1, 1, 0) == 1` — i.e., a stake-to-total-stake ratio of 2 gets silently capped to `pending_delegator_rewards` rather than erroring) [5](#0-4) . A concrete triggering scenario (e.g. reconstructing exact stake-history/vote-account state during bank recalculation such that `total_active_stake` undercounts real effective stake) would require live-cluster/simulation testing that is out of scope for static code review; a background Devin session with test-harness access would be needed to construct and verify an end-to-end reproduction.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L211-231)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L582-608)
```rust
    fn get_epoch_params_for_recalculation<'a>(
        &'a self,
        rewarded_epoch: Epoch,
        stakes: &'a Stakes<StakeAccount<Delegation>>,
    ) -> EpochRewardCalculateParamInfo<'a> {
        // Use `stakes` for stake-related info
        let stake_history = stakes.history().clone();
        let stake_delegations = stakes.stake_delegations_vec();

        // Use the VAT-filtered vote-account snapshot from epoch_stakes.
        // Recalculation should match the vote-account admission policy used for
        // distribution.
        let leader_schedule_epoch = self.epoch_schedule().get_leader_schedule_epoch(self.slot());
        let distribution_epoch_vote_accounts = self
            .epoch_stakes(leader_schedule_epoch)
            .expect("calculation should always run after Bank::update_epoch_stakes()")
            .stakes()
            .vote_accounts();
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, distribution_epoch_vote_accounts);

        EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L815-833)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4320-4332)
```rust
    #[test]
    fn test_calculate_block_reward_specific() {
        // get nothing
        assert_eq!(get_block_reward_for_test(0, 0, 0, 0), 0);
        // get everything
        assert_eq!(get_block_reward_for_test(1, 1, 1, 0), 1);
        // individual stake higher than block reward, capped
        assert_eq!(get_block_reward_for_test(2, 1, 1, 0), 1);
        // not truncated
        assert_eq!(get_block_reward_for_test(1, 10, 10, 0), 1);
        // truncated
        assert_eq!(get_block_reward_for_test(1, 10, 9, 0), 0);
    }
```
