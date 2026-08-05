## Title
Block-revenue-sharing reward mis-attributes `pending_delegator_rewards` across delegations to the same vote account, allowing aggregate over-payment beyond the vote account's actual deposited revenue - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The reported bug is a classic "fee/share computed from the wrong basis" defect: shares were minted proportional to a gross input value instead of the actual profit, letting one party collect more than was ever deposited, at the expense of everyone else holding a claim on the same pool. The closest analog inside Agave is `calculate_block_reward()` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-232`, which computes each individual stake account's share of a vote account's `pending_delegator_rewards` pool using `stake / total_active_stake`, then clamps only that individual result to `pending_delegator_rewards`. There is no clamp on the *sum* of all such shares across every delegation pointing at the same vote account, so the aggregate amount paid out for one vote account's block-revenue pool can exceed the actual `pending_delegator_rewards` that was ever deposited into it.

### Finding Description
`calculate_block_reward()` computes, per stake delegation: [1](#0-0) 

```
let total_active_stake = reward_epoch_delegated_stakes.delegated_stakes.get(&vote_pubkey)...
...
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into()
    .unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```

The function's own comment acknowledges the invariant is fragile: [2](#0-1) 

> "During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`. ... We can also have individual rewards look greater than the pending rewards. This is harmless in practice, but we clamp it just to be safe"

The critical detail is that the `.min(pending_delegator_rewards)` clamp is applied **independently, per delegation**, not against a running total consumed from the vote account's pool. `total_active_stake` is a value frozen at `reward_epoch_delegated_stakes` (captured once, at reward-epoch boundary), while the numerator `stake` is recomputed per-delegation via `delegation_effective_stake()` at the time of redemption/recalculation. If the per-delegation `stake` values used at redemption diverge from what was true when `total_active_stake` was captured (which the comment explicitly says can happen "during recalculation"), then `sum(stake_i) > total_active_stake` is possible, and because each `reward_i = min(pending_delegator_rewards * stake_i / total_active_stake, pending_delegator_rewards)` is clamped only against the *whole* pool value rather than the *remaining* pool value, the sum of `reward_i` across all delegations to that vote account can legitimately exceed `pending_delegator_rewards`.

This callsite is invoked once per stake delegation inside the parallel reward computation loop, with no shared/decrementing accumulator for the vote account's pool: [3](#0-2) 

Each stake account's `block_reward` is summed into `block_reward_lamports_distributed` and paid to the stake account independently in `store_stake_accounts_in_partition` / `build_updated_stake_reward` (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs`), with no cross-check that the total paid for a given vote account does not exceed that vote account's actual `pending_delegator_rewards` field (which is only decremented by the withdraw path in the vote program, not tied back to reward distribution accounting here).

Existing guards do not stop this path:
- The per-delegation `.min(pending_delegator_rewards)` bounds *one* stake account's payout to the entire pool, but not the *aggregate* payout across all stake accounts delegated to the same validator.
- `total_active_stake` is a stale snapshot (`RewardEpochDelegatedStakes`, captured at end of reward epoch) while `stake` is recomputed live via `delegation_effective_stake`, so the two can be inconsistent, especially across the "recalculation" path explicitly called out in the code comment (`recalculate_stake_rewards` in the same file, lines 1038-1095), which re-derives stake rewards from a potentially different bank state than the original calculation.

### Impact Explanation
If the aggregate of per-delegation block-revenue shares for a single vote account exceeds that vote account's `pending_delegator_rewards`, the runtime mints new lamports for stakers that were never actually deposited by the validator via `DepositDelegatorRewards`. Because every validator executes this same deterministic calculation over the same state, this does not cause a fork by itself, but it is a real fund-inflation/theft bug: value is created out of thin air and paid to some stakers, diluting the value of SOL held by everyone else and silently inflating `capitalization` beyond what block-revenue-sharing deposits actually justify. This is the direct analog of TOKE-14: a share/fee calculated from the wrong basis (a stale total instead of the true remaining balance) causing an excess payout that comes at other participants' expense.

### Likelihood Explanation
The trigger condition is explicitly acknowledged by the code's own comment: `stake > total_active_stake` "during recalculation" — this is a normal, unprivileged occurrence any time a stake account has already accrued a partial reward before a bank recalculates rewards (e.g., during `recalculate_partitioned_rewards_if_active` / `recalculate_stake_rewards`, which can run on ordinary epoch-boundary or restart-driven recomputation, not requiring a malicious actor). No malicious peer, validator, or privileged action is required — the divergence arises from ordinary state evolution between when `RewardEpochDelegatedStakes` is snapshotted and when block rewards are redeemed/recalculated for individual delegations. However, I was not able to fully trace whether `total_stake_rewards_lamports`/`total_block_reward` bookkeeping elsewhere clamps the *sum* before it is ever stored (the search for a global "sum <= pending_delegator_rewards" check across `distribution.rs` was inconclusive within the available context), so the exact severity of real-world overpay (versus being masked by a downstream aggregate check I could not locate) is not fully confirmed.

### Recommendation
Add a global, vote-account-scoped clamp: when computing block rewards for a set of delegations, track cumulative `block_reward` already attributed to each vote account, and clamp each delegation's share to `pending_delegator_rewards.saturating_sub(already_attributed)`, rather than clamping each share independently against the full, un-decremented `pending_delegator_rewards`. Alternatively, recompute `total_active_stake` consistently with the `stake` values actually used at redemption time (rather than relying on a frozen `RewardEpochDelegatedStakes` snapshot that can diverge during recalculation), so `sum(stake_i) <= total_active_stake` is a true invariant.

### Proof of Concept
Not independently reproduced against a live cluster from local code alone; the argument is derived from the code's own acknowledged failure mode:
1. Vote account V has `pending_delegator_rewards = R` deposited via `DepositDelegatorRewards` (SIMD-0123).
2. `RewardEpochDelegatedStakes` snapshots `total_active_stake = T` for V at reward-epoch boundary.
3. Two delegations D1, D2 to V exist. During a recalculation pass (`recalculate_stake_rewards`), `delegation_effective_stake` for D1 and D2 (computed live) sums to `S > T`, per the code's own documented possibility ("stake > total_active_stake ... during recalculation").
4. `calculate_block_reward` computes for D1: `min(R * stake_D1 / T, R)`, and independently for D2: `min(R * stake_D2 / T, R)`. Because `stake_D1 + stake_D2 > T`, `R * stake_D1/T + R * stake_D2/T > R`, and since each term is only capped at `R` individually (not at the remaining balance), the sum paid to D1 and D2 combined can exceed `R`, minting reward lamports never deposited for V.
5. The included unit test `test_calculate_block_reward_specific`/`test_calculate_block_reward_prop` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:4320-4347`) only asserts a *single* call's output is `<= pending_delegator_rewards`; it never asserts the sum across multiple delegations for the same vote account is bounded, so this aggregate-overpay path is not covered by existing tests. [4](#0-3)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4334-4347)
```rust
    proptest! {
        #[test]
        fn test_calculate_block_reward_prop(
            individual_stake in 0..=u64::MAX,
            total_stake in 0..=u64::MAX,
            pending_delegator_rewards in 0..=u64::MAX,
            rewarded_epoch in 0..=solana_stake_history::MAX_ENTRIES as u64,
        ) {
            let reward = get_block_reward_for_test(individual_stake, total_stake, pending_delegator_rewards, rewarded_epoch);
            // This check is pedantic since the code clamps the output, so the
            // test is checking for panics.
            prop_assert!(reward <= pending_delegator_rewards);
        }
    }
```
