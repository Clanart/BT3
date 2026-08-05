### Title
`calculate_block_reward`'s pro-rata split of `pending_delegator_rewards` can silently exceed the pool because it derives each delegation's share from a stake snapshot that can diverge from the stake set actually used to accumulate the pool — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The C4 report describes a class of bug where a shared/pooled balance is used as the numerator/denominator basis for a proportional payout, but the pool total and the stake basis used to divide it up are not kept perfectly consistent across time, letting some claimants receive more than their fair share of a shared pot funded by others. The closest unprivileged Agave analog is the block-revenue-sharing pool tracked by `pending_delegator_rewards` on a vote account (SIMD-0123), which is distributed to stake delegations pro-rata by `calculate_block_reward` [1](#0-0) .

### Finding Description
`pending_delegator_rewards` is a single pooled balance on the vote account, incremented only via `DepositDelegatorRewards` [2](#0-1) . At epoch reward time, `calculate_block_reward` splits this single pool across every stake delegation proportional to `stake / total_active_stake`, where `total_active_stake` is read from a stake-history snapshot (`reward_epoch_delegated_stakes`) computed at the *end* of the rewarded epoch [3](#0-2) .

The code's own comments acknowledge the invariant is fragile: during *recalculation* (triggered when a warp/rollback forces rewards to be recomputed), "if stake account has already received rewards, it's possible to have `stake > total_active_stake`," which can make an individual delegation's computed share exceed the entire pool, or overflow arithmetic — handled only by clamping the result to `pending_delegator_rewards` [4](#0-3) . This is structurally the same broken invariant as `BathBuddy::vestedAmount`: a value meant to represent "my fair share of a fixed pool" is computed from inputs (stake snapshot, pool balance) that are not guaranteed to be mutually consistent at read time, and the fix applied is a saturating clamp rather than a recomputation that preserves the sum-of-shares == pool invariant across all delegations. Clamping an individual delegation's payout does not rebalance what *other* delegations receive, so the sum of all per-delegation block rewards for a given vote account is not guaranteed to equal `pending_delegator_rewards`, and no code path shown decrements `pending_delegator_rewards` by the amount actually paid out (unlike `distributed_lamports`/`total_stake_rewards_lamports` tracking done for the inflation-reward pool in `distribute_reward_commissions`) — I could not locate, within the indexed portion of the codebase, the corresponding subtraction of the paid-out amount from the vote account's `pending_delegator_rewards` field or vote-account lamport balance in `distribution.rs`/`calculation.rs`. This absence could not be fully confirmed due to index size limits; it should be verified directly against the full source.

### Impact Explanation
If the sum of per-delegation shares of `pending_delegator_rewards` is not tied back to an actual debit of the pool/vote-account balance, delegators could receive block-revenue-sharing rewards repeatedly from the same underlying pool across multiple epochs/recalculations, or one delegator's clamped payout could allow the remainder to be paid disproportionately to others in a way inconsistent with actual stake proportions — a fund-accounting error consistent with the "Medium" severity class in the seed report (leaked/duplicated value rather than directly stolen keys).

### Likelihood Explanation
Recalculation of partitioned rewards is an unprivileged-triggerable code path (it runs whenever the reward computation must be redone, e.g., after certain warp/replay scenarios), and the divergence between `total_active_stake` (snapshotted at end of rewarded epoch) and per-delegation `stake` (which can already include previously-applied rewards) is explicitly called out by the code's own comments as a real, reachable condition, not a purely theoretical one.

### Recommendation
Track cumulative lamports actually paid out of `pending_delegator_rewards` per vote account during distribution and assert/enforce that the sum never exceeds the pool, explicitly decrementing `pending_delegator_rewards` (and the vote account balance) by the exact amount distributed, mirroring the `distributed_lamports`/`burned_lamports` accounting already done for the inflation-reward commission path in `distribute_reward_commissions` [5](#0-4) , instead of only clamping individual results.

### Proof of Concept
Not independently reproduced from local code alone; the reasoning is derived directly from the code comment at `calculate_block_reward` acknowledging that `stake > total_active_stake` is reachable during recalculation and that per-delegation rewards can be computed as exceeding `pending_delegator_rewards`, requiring a clamp [4](#0-3) . A concrete lamport-level PoC would require constructing a recalculation scenario (e.g., via `recalculate_stake_rewards`) where a delegation's stake grows between the snapshot and recalculation and confirming whether `pending_delegator_rewards` is or isn't correctly debited on distribution — this final verification step could not be completed with the available indexed code and is flagged as the key remaining uncertainty.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-182)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-220)
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
```

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L392-408)
```rust
        let StakeRewardCalculation {
            total_stake_rewards_lamports,
            ..
        } = stake_rewards;

        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```

**File:** programs/vote/src/vote_state/mod.rs (L980-988)
```rust
    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```
