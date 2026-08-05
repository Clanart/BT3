Based on my investigation, I found a genuine analog to the Yieldy "clamp-but-still-use-uncapped-value" bug pattern in Agave's block reward calculation logic.

### Title
`calculate_block_reward` clamps individual delegator rewards to `pending_delegator_rewards` per stake account without decrementing the shared pool, allowing per-vote-account over-distribution across multiple delegators - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`calculate_block_reward` computes a stake account's share of a vote account's `pending_delegator_rewards` pool as `pending_delegator_rewards * stake / total_active_stake`, then clamps the result with `.min(pending_delegator_rewards)` — exactly mirroring the Yieldy pattern of clamping a computed value to a maximum without reducing the "profit"/pool value used for subsequent computations. [1](#0-0) 

### Finding Description
The function is called independently, once per stake delegation, inside a parallel iterator over `stake_delegations` in `calculate_stake_rewards_and_commissions`: [2](#0-1) 

Each call reads the *same, unmodified* `pending_delegator_rewards` value from the vote account state and computes its own share independently — there is no shared, decrementing accumulator that reduces the pool as it is consumed by earlier delegators (unlike, e.g., `TokenBucket::consume_tokens`, which does track state consumption). The code's own comment acknowledges the sub-invariant can be violated: *"During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`... individual rewards look greater than the pending rewards... we clamp it just to be safe"* [3](#0-2) .

This is structurally identical to the Yieldy bug: a value (`updatedTotalSupply`/here, an individual delegator's computed reward) is clamped to a maximum (`MAX_SUPPLY`/here, `pending_delegator_rewards`), but the downstream bookkeeping (`_storeRebase`'s `_profit`/here, the sum credited across all `stake_reward_amount` and `block_reward_amount` per delegator) still treats each independent computation as if the full pool were available to it, rather than subtracting what's already been allocated to prior delegators.

### Impact Explanation
If one delegation's effective `stake` (computed via `delegation_effective_stake`) is inflated relative to `total_active_stake` — which the code's own comment says is possible during reward *recalculation* — that single stake account can be credited with the *entire* `pending_delegator_rewards` pool via the `.min()` clamp, while every *other* delegator to the same vote account computes its share from the same un-decremented `pending_delegator_rewards`/`total_active_stake` ratio. The resulting `block_reward` amounts are summed into `stake_reward_lamports_minted`/`block_reward_lamports_distributed` and fed directly into `self.capitalization.fetch_add(...)` in `distribute_epoch_rewards_in_partition` [4](#0-3) , and into each stake account's lamport balance via `checked_add_lamports` in `build_updated_stake_reward` [5](#0-4) .
This is a fund-inflation vector: capitalization and account balances can be minted beyond the value actually backed by `pending_delegator_rewards`, corrupting the total-supply invariant checked elsewhere (e.g., `calculate_capitalization_at_startup_from_index`).

### Likelihood Explanation
This is speculative and unverified — I could not find, within the indexed portion of the codebase, the exact conditions under which `delegation_effective_stake` for a single delegation can exceed `total_active_stake` in production (only the code comment acknowledges the possibility "during recalculation"), nor could I confirm whether any caller-side invariant (e.g., in `redeem_delegation_rewards` or the reward-commission `assert!` in `distribute_reward_commissions`) fully closes this gap in practice for the block-reward path specifically. The existing property test `test_calculate_block_reward_prop` only asserts `reward <= pending_delegator_rewards` for a *single* delegation call, and does not test the multi-delegator aggregate case, so the described over-allocation is untested at present. [6](#0-5) 

### Recommendation
Track and decrement a running total per vote account across all its delegations during the parallel reward-calculation pass (or compute total requested block rewards for each vote account first, then pro-rate/clamp against the *actual remaining* `pending_delegator_rewards`), rather than clamping each delegation's share independently against the full, undiminished pool value.

### Proof of Concept
Not fully constructible from local code alone — this would require reproducing the exact circumstance (described only in comments) where `delegation_effective_stake` for one delegation exceeds `total_active_stake` during epoch-reward recalculation, together with a second co-delegated stake account, then observing that `sum(block_reward across delegators) > pending_delegator_rewards`. I was not able to locate a concrete repro path or feature flag toggling this in the indexed code, so this should be validated by a background engineer with full repository/test access (e.g., via `test_calculate_block_reward_prop`-style extension to multiple delegations) before being treated as confirmed.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L221-230)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-198)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```
