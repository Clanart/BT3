## Title
`pending_delegator_rewards` is never decremented when block rewards are paid out, allowing repeated/duplicate distribution of the same delegator reward pool across epochs - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
This is the closest Agave analog to the reported Hyper `syncPositionFees` bug: a shared reward pool value (`pending_delegator_rewards` on a vote account, funded via `DepositDelegatorRewards`) is repeatedly used as the numerator for a proportional payout to every staker, but nowhere in the reviewed reward-calculation/distribution code path is that pool value reduced by the amounts already paid out. The unit vs. total math itself (`stake * pending_delegator_rewards / total_active_stake`) is implemented correctly — this differs from the original report's core flaw — but the *lack of pool depletion* reproduces the same underlying invariant violation: stakers can be paid out of a shared pot repeatedly instead of the pot decreasing as it's consumed.

### Finding Description
`calculate_block_reward` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-232` computes each stake account's share of block revenue as: [1](#0-0) 

It reads `pending_delegator_rewards` directly from the vote account's state via `vote_state.pending_delegator_rewards()` [2](#0-1)  and multiplies it by each delegator's `stake / total_active_stake` fraction. This part correctly mirrors the fix recommended in the external report (using the caller's own stake, not the total pool, as numerator).

However, `pending_delegator_rewards` is only ever *increased*, via `add_pending_delegator_rewards` in `programs/vote/src/vote_state/handler.rs:196-209`, called from `deposit_delegator_rewards` in `programs/vote/src/vote_state/mod.rs:936-988` (the `DepositDelegatorRewards` instruction handler in `programs/vote/src/vote_processor.rs:409-426`). Across the full grep of the codebase, there is no call site that decrements/subtracts from `pending_delegator_rewards` after `calculate_block_reward` pays it out to stakers each epoch. The reward-distribution path (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs:239-424`) only credits `block_reward` lamports to each stake account and updates capitalization/metrics; it never writes back to the vote account's `pending_delegator_rewards` field.

This means the value read by `calculate_block_reward` at the start of every rewarded epoch is the *cumulative, ever-growing* deposit total rather than an "unclaimed" balance. If the vote account operator deposits a pool of delegator rewards once, `calculate_block_reward` will use that same undiminished pool as the reward basis in every subsequent epoch until stake changes or the vote account is re-inspected — effectively distributing far more in cumulative block rewards than was ever deposited, funded by minting new lamports into stake accounts (`account.checked_add_lamports(partitioned_stake_reward.block_reward)` in `distribution.rs:266-267`).

### Impact Explanation
If confirmed by full-path tracing (see caveats below), this would let a validator/vote account continuously "double-spend" a single delegator reward deposit: each epoch, stakers backing that vote account receive additional lamports proportional to the *entire* un-decremented `pending_delegator_rewards` value, even though only one real transfer of that size ever occurred. This inflates capitalization/mints unearned lamports every epoch (fund creation/theft from protocol economics), which is a network-wide funds-integrity violation rather than a local account exploit — it does not require a malicious validator to cause harm to others, only requires normal reward-epoch processing to run against a vote account that ever called `DepositDelegatorRewards` once.

### Likelihood Explanation
Likelihood is **uncertain** given the scope of code actually inspected. I was not able to fully trace (within the available tool budget) whether:
- `pending_delegator_rewards` is deducted somewhere I did not find (e.g., inside `store_stake_accounts_in_partition`, `deposit_or_burn_fee`, or a separate vote-account-state write during `distribute_epoch_rewards_in_partition` that I didn't inspect line-by-line for vote-account mutation), or
- the design intentionally treats `pending_delegator_rewards` as a perpetual "commission rate pool" rather than a single deposit to be drawn down (in which case this is expected behavior, not a bug).

Given SIMD-0123's design intent (named "delegator rewards" deposit, singular, matched by `DepositDelegatorRewards`), a persistent non-decrementing field strongly suggests either (a) a missing subtraction that would constitute a critical fund-duplication bug, or (b) code elsewhere (not found by my searches) that resets/decrements this field that I could not locate due to tool-call limits.

### Recommendation
Trace every write path to the vote account's `pending_delegator_rewards` field (including any not surfaced by the `pending_delegator_rewards` text grep, e.g. via raw byte offsets in `vote_state_view/frame_v4.rs`, which stores it at a fixed offset and could be mutated without referencing the field name in Rust). Confirm whether the distribution path ever calls something equivalent to `set_pending_delegator_rewards` or performs a raw account write reducing the value by `block_reward` amounts paid out that epoch. If no such subtraction exists, the reward calculation must decrement `pending_delegator_rewards` by the aggregate `block_reward` distributed for that vote account each epoch, mirroring the report's recommendation to use the actual remaining share (not the total historical deposit) as the basis for each epoch's payout.

### Proof of Concept
Not conclusively demonstrable from the index alone. A concrete PoC would require constructing a bank/vote-account test (similar to `test_calculate_block_reward_specific` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:4320-4332`) that: (1) deposits lamports via `DepositDelegatorRewards`, (2) advances two reward epochs with `block_revenue_sharing` enabled, and (3) asserts whether `pending_delegator_rewards` (and the vote account's real lamport balance) decreases proportionally to the block rewards actually paid out in epoch 1 before epoch 2's calculation runs. I was unable to execute or fully trace this within the available search iterations, so this finding should be treated as a lead requiring direct code/test verification in a Devin session with full file access, not a confirmed vulnerability.

**Given the uncertainty above, I am not confident enough in this being a definitively confirmed, exploitable bug to assert it categorically — it is offered as the strongest structural analog found, with an explicit caveat that the decrement path may exist elsewhere in code not covered by my search.**

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L188-189)
```rust
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
```

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
