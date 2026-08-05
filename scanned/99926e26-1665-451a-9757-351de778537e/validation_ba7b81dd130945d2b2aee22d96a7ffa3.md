## Title
Per-delegator floor-division in `calculate_block_reward` permanently strands lamports from `pending_delegator_rewards` - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`calculate_block_reward` splits a vote account's `pending_delegator_rewards` pool across all of its delegating stake accounts proportionally to `stake / total_active_stake`, using `u128` multiplication followed by integer division. This is structurally the same pattern as the Covalent `DelegatedStaking.depositRewardTokens` bug: an amount is divided across N recipients and each recipient's share is floored, so the sum of all distributed shares is `<= pending_delegator_rewards`, with the shortfall growing with the number of delegators rather than being capped at "1 unit" as in the vote/stake commission-split code elsewhere in the same codebase.

### Finding Description [1](#0-0) 
computes each delegator's share of the pool with:
```rust
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into()
    .unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```
This is invoked once per stake delegation in `calculate_stake_rewards_and_commissions` [2](#0-1) , independently for every stake account delegated to the same vote account, with no accumulator that tracks the running remainder or that assigns leftover dust to the last delegator (unlike `commission_split_preserve_lamports`, which explicitly computes one side by subtraction from the total to guarantee the split is lossless, see `runtime/src/inflation_rewards/mod.rs:413-435`).

Because `stake / total_active_stake` for each delegator is rounded down independently, the sum of all per-delegator `block_reward` values is generically strictly less than `pending_delegator_rewards`. For example, with `pending_delegator_rewards = 10` and three delegators each holding `stake = 1` out of `total_active_stake = 3`: each delegator computes `10*1/3 = 3` (floor), so `3*3 = 9` is distributed while `1` lamport is never distributed to anyone.

The existing test suite explicitly documents and accepts single-delegator truncation (`get_block_reward_for_test(1, 10, 9, 0) == 0` — "truncated") but does not check the *aggregate* loss across multiple concurrent delegators to the same vote account, which is the actual attack/impact surface.

### Impact Explanation
Following `redeem_delegation_rewards` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:701-775`), the `block_reward` computed per stake account is credited to that stake account in `build_updated_stake_reward` (`distribution.rs:262-267`). There is no code path that returns the undistributed remainder of `pending_delegator_rewards` back to the vote account, back to the staker, or to the incinerator/burn accounting used elsewhere for rounding discrepancies (contrast with `distribute_reward_commissions`'s explicit `burned_lamports`/`distributed_to_incinerator_lamports` accounting, `calculation.rs:384-414`). The residual lamports remain nominally "owed" (they stay inside `pending_delegator_rewards`, which is a vote-account lamport-backed balance guarded by `withdraw()`'s `min_balance` check, `programs/vote/src/vote_state/mod.rs:1113-1121`) but are never paid out to the stakers who actually earned them and cannot be withdrawn by the validator either, because the withdraw guard keeps `pending_delegator_rewards` reserved. This is a loss-of-yield issue analogous to the Medium-severity Covalent finding: no funds are stolen or double-spent, but rightfully-earned staker rewards are permanently unpaid/stuck, scaling with the number of delegators per vote account and repeating every epoch that block-revenue-sharing rewards are distributed (feature `block_revenue_sharing`).

### Likelihood Explanation
This code path executes unconditionally whenever the `block_revenue_sharing` feature is active and a vote account has `pending_delegator_rewards > 0` with more than one active delegator — a completely ordinary, permissionless validator/staking configuration requiring no malicious actor. The larger the delegator count for a popular vote account, the larger the aggregate truncation per epoch, making the loss systematic and continuous rather than a rare edge case.

### Recommendation
Track the running sum of already-distributed `block_reward` per vote account across its delegators and, for the last delegator (or via a remainder-preserving split similar to `commission_split_preserve_lamports`), assign the true remainder (`pending_delegator_rewards - sum_of_previous_shares`) instead of independently flooring each delegator's proportional share. Alternatively, accumulate the per-vote-account truncation dust and either roll it into the next epoch's `pending_delegator_rewards` or explicitly account for it (burn/incinerate) the way `distribute_reward_commissions` already does for other rounding remainders, so capitalization and reward accounting stay exactly balanced.

### Proof of Concept
1. Enable `block_revenue_sharing`; create a vote account `V` with `pending_delegator_rewards = 10`.
2. Delegate three stake accounts `S1, S2, S3` to `V`, each with active stake `1`, so `total_active_stake = 3`.
3. At epoch boundary, `calculate_block_reward` is invoked once per delegation:
   - `S1`: `10 * 1 / 3 = 3`
   - `S2`: `10 * 1 / 3 = 3`
   - `S3`: `10 * 1 / 3 = 3`
4. Total distributed = `9`, but the pool was `10`. Follow the existing unit test pattern in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:4246-4347` (`get_block_reward_for_test`) but sum the results of calling it independently for `S1..S3` against the same `pending_delegator_rewards`/`total_active_stake` inputs to confirm the aggregate shortfall — no code path retrieves or reallocates the missing `1` lamport.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
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
```
