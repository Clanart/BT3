## Title
Unchecked subtraction in epoch-reward credit accounting can underflow (`u64` panic / wraparound) - (File: `runtime/src/inflation_rewards/points.rs`)

## Summary
The external report describes an `a - b` computation (`newRatio = a - b`) whose designer implicitly assumed `a >= b`, but a time-dependent user action could push `b` above `a`, making the subtraction fail/underflow and permanently DoS the caller. The closest structural analog in Agave is `calc_earned_credits()` in `runtime/src/inflation_rewards/points.rs`, which performs two **raw, unchecked `u64` subtractions** (`final_epoch_credits - initial_epoch_credits` and `final_epoch_credits - *new_credits_observed`) instead of the `saturating_sub`/`checked_sub` idiom used almost everywhere else in the codebase for credit/epoch arithmetic (e.g. `credits.saturating_sub(prev_credits)` in `cli-output/src/cli_output.rs:2049`, `final_credits.saturating_add(...)` in `runtime/src/block_component_processor/vote_reward.rs`). [1](#0-0) 

## Finding Description
`calc_earned_credits` computes how many vote credits a stake account "earned" during an epoch by comparing the stake's previously-observed credits (`stake.credits_observed`) against the vote account's recorded `(epoch, final_epoch_credits, initial_epoch_credits)` tuple:

```rust
fn calc_earned_credits(
    stake: &Stake,
    final_epoch_credits: u64,
    initial_epoch_credits: u64,
    new_credits_observed: &mut u64,
) -> u128 {
    let credits_in_stake = stake.credits_observed;

    let earned_credits = if credits_in_stake < initial_epoch_credits {
        final_epoch_credits - initial_epoch_credits
    } else if credits_in_stake < final_epoch_credits {
        final_epoch_credits - *new_credits_observed
    } else {
        0
    };
    *new_credits_observed = (*new_credits_observed).max(final_epoch_credits);
    u128::from(earned_credits)
}
``` [1](#0-0) 

Both subtractions rely on the invariant that the left operand is always `>=` the right operand — exactly the same class of unguarded assumption as the reported `a - b` bug. This function is invoked from `tower_epoch_credits_iter` (which iterates every `(epoch, final, initial)` tuple in a vote account's `epoch_credits` history) and from `calculate_alpenglow_points` (which tracks a *separately re-initialized* `new_credits_observed` starting again from `stake.credits_observed` for the post-migration segment): [2](#0-1) [3](#0-2) 

and combined for the Alpenglow-migration epoch in `calculate_migration_points`, which restarts `new_credits_observed` tracking for the Alpenglow portion independently of the tower loop's running value: [4](#0-3) 

The safety of these subtractions depends entirely on the vote account's `epoch_credits` vector always being a contiguous, monotonically non-decreasing chain (`initial_credits[n+1] == final_credits[n]`) and on `new_credits_observed` never being advanced past a `final_epoch_credits` it is later subtracted from. This invariant is *not enforced* at the point of subtraction (no `checked_sub`/`saturating_sub`/assertion) — it is only true if every other part of the vote/reward pipeline (vote credit increments, `MAX_EPOCH_CREDITS_HISTORY` trimming, Alpenglow-migration marker insertion in `runtime/src/block_component_processor/vote_reward.rs`) preserves it perfectly across all code paths, including the migration-epoch special-casing where tower and Alpenglow credit tracking are computed with independently-initialized `new_credits_observed` values.

## Impact Explanation
`calc_earned_credits` runs unconditionally for every delegated stake account during partitioned epoch-reward calculation (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), which is mandatory validator runtime code executed identically by every node at every epoch boundary. If any input combination (e.g., through the Tower→Alpenglow migration-epoch bookkeeping, where two independently-tracked `new_credits_observed` values must remain consistent with the shared `epoch_credits` vector) ever violates the assumed ordering, the subtraction underflows. In a Rust `u64` computation, an underflow either:
- panics (if built with overflow checks — common for Solana/Agave release-safety builds), causing a **synchronized crash of every validator computing rewards for that epoch**, i.e., a consensus halt, or
- wraps to a near-`u64::MAX` value if overflow checks are disabled, producing an astronomically large "earned credits" count that flows into `points` (`u128`) and ultimately into `staker_rewards`/`voter_rewards` lamport payouts — a **fund-inflation / false-execution** bug.

Both outcomes are in-scope impacts (consensus halt or false execution/fund theft) for `runtime`/`accounts` code per the task's Valid Impact list.

## Likelihood Explanation
This is **not confirmed exploitable from local code alone** — I was not able to fully trace, within the available tool budget, an unprivileged path that forces `epoch_credits` non-contiguity or forces `new_credits_observed` to exceed a subsequent `final_epoch_credits` (the normal vote-credit increment logic in `programs/vote/src/vote_state/handler.rs::increment_credits` and the Alpenglow marker logic in `runtime/src/block_component_processor/vote_reward.rs::increment_credits` both appear structured to preserve the chain invariant in the paths I inspected). The Alpenglow migration epoch is the most likely place where the invariant could be violated, because it introduces a second, independently-initialized `new_credits_observed` tracker that must stay consistent with a chain crossing the `AG_MIGRATION_EPOCH_CREDIT` marker — but I could not verify this exhaustively (e.g., against delinquent validators with sparse voting history, `MAX_EPOCH_CREDITS_HISTORY` trimming interactions, or vote-account resets/reinitializations mid-epoch) within the available search iterations. I also could not confirm from the codebase whether the runtime crate is built with `overflow-checks = true` (no `Cargo.toml` setting was found via search), which determines whether this would manifest as a panic (consensus halt) or a silent wraparound (fund inflation).

## Recommendation
Replace the raw `-` operators in `calc_earned_credits` with `checked_sub` (returning an explicit error/`None` path propagated up through `calculate_stake_points_and_credits`) or, at minimum, `saturating_sub` with an accompanying `debug_assert!`/metric to detect invariant violations without risking a production panic or silent wraparound, consistent with the pattern already used elsewhere in this file's sibling functions (e.g. `saturating_add` in `vote_reward.rs`). Add unit/property tests that specifically exercise the Tower→Alpenglow migration-epoch boundary with adversarial/sparse vote-credit histories to confirm the invariant holds for all reachable `epoch_credits` shapes.

## Proof of Concept
Not available — I could not construct a concrete, verified sequence of vote/stake operations from local code alone that drives `credits_in_stake`/`new_credits_observed`/`final_epoch_credits` into the underflow condition; doing so would require either deeper tracing of the Alpenglow migration bookkeeping or dynamic testing (e.g., a Devin session with fuzzing/property-testing of `calculate_stake_points_and_credits` across synthetic `epoch_credits` histories, including migration-marker edge cases) beyond what is feasible in this read-only investigation.

### Citations

**File:** runtime/src/inflation_rewards/points.rs (L158-181)
```rust
fn calc_earned_credits(
    stake: &Stake,
    final_epoch_credits: u64,
    initial_epoch_credits: u64,
    new_credits_observed: &mut u64,
) -> u128 {
    let credits_in_stake = stake.credits_observed;

    // figure out how much this stake has seen that
    //   for which the vote account has a record
    let earned_credits = if credits_in_stake < initial_epoch_credits {
        // the staker observed the entire epoch
        final_epoch_credits - initial_epoch_credits
    } else if credits_in_stake < final_epoch_credits {
        // the staker registered sometime during the epoch, partial credit
        final_epoch_credits - *new_credits_observed
    } else {
        // the staker has already observed or been redeemed this epoch
        //  or was activated after this epoch
        0
    };
    *new_credits_observed = (*new_credits_observed).max(final_epoch_credits);
    u128::from(earned_credits)
}
```

**File:** runtime/src/inflation_rewards/points.rs (L187-234)
```rust
fn tower_epoch_credits_iter(
    stake: &Stake,
    epoch_credits_iter: impl Iterator<Item = (Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> (u128, u64, bool) {
    let mut points = 0;
    let credits_in_stake = stake.credits_observed;
    let mut new_credits_observed = credits_in_stake;
    let mut saw_marker = false;

    for entry in epoch_credits_iter {
        if entry == AG_MIGRATION_EPOCH_CREDIT {
            saw_marker = true;
            break;
        }
        let (epoch, final_epoch_credits, initial_epoch_credits) = entry;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));

        // finally calculate points for this epoch
        let earned_points = stake_amount * earned_credits;
        points += earned_points;

        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CalculatedPoints(
                epoch,
                stake_amount,
                earned_credits,
                earned_points,
            ));
        }
    }
    (points, new_credits_observed, saw_marker)
}
```

**File:** runtime/src/inflation_rewards/points.rs (L243-312)
```rust
fn calculate_alpenglow_points(
    stake: &Stake,
    reward_epoch_credits: Option<(Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
    reward_epoch_delegated_stakes: &RewardEpochDelegatedStakes,
) -> Result<(u128, u64), CalculatedStakePoints> {
    let Some((epoch, final_epoch_credits, initial_epoch_credits)) = reward_epoch_credits else {
        return Ok((0, stake.credits_observed));
    };
    if epoch != reward_epoch_delegated_stakes.epoch {
        // In this case, the vote account did not record any credits in this epoch
        // The latest entry is from a prior epoch - thus the delegation gets 0 rewards
        return Ok((0, stake.credits_observed));
    }

    let (earned_credits, new_credits_observed) = {
        let mut new_credits_observed = stake.credits_observed;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        (earned_credits, new_credits_observed)
    };

    let stake_amount = u128::from(delegation_effective_stake(
        &stake.delegation,
        epoch,
        stake_history,
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    ));

    let earned_points = if earned_credits == 0 || stake_amount == 0 {
        0
    } else {
        let Some(total_stake) = reward_epoch_delegated_stakes
            .delegated_stakes
            .get(&stake.delegation.voter_pubkey)
            .copied()
            .filter(|stake| *stake != 0)
        else {
            record_error(format!(
                "AG delegated stake denominator for vote_pubkey={} in epoch={} failed",
                stake.delegation.voter_pubkey, reward_epoch_delegated_stakes.epoch
            ));
            return Err(CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed,
                force_credits_update_with_skipped_reward: true,
            });
        };
        earned_credits * stake_amount / total_stake as u128
    };

    if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
        inflation_point_calc_tracer(&InflationPointCalculationEvent::CalculatedPoints(
            epoch,
            stake_amount,
            earned_credits,
            earned_points,
        ));
    }
    Ok((earned_points, new_credits_observed))
}
```

**File:** runtime/src/inflation_rewards/points.rs (L319-352)
```rust
fn calculate_migration_points(
    stake: &Stake,
    mut epoch_credits_iter: impl Iterator<Item = (Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
    reward_epoch_delegated_stakes: &RewardEpochDelegatedStakes,
) -> Result<(u128, u128, u64), CalculatedStakePoints> {
    let (tower_points, tower_new_credits_observed, saw_marker) = tower_epoch_credits_iter(
        stake,
        epoch_credits_iter.by_ref(),
        stake_history,
        inflation_point_calc_tracer.as_ref(),
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    );
    let (ag_points, ag_new_credits_observed) = if saw_marker {
        calculate_alpenglow_points(
            stake,
            epoch_credits_iter.next(),
            stake_history,
            inflation_point_calc_tracer,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
            reward_epoch_delegated_stakes,
        )?
    } else {
        (0, stake.credits_observed)
    };

    let new_credits_observed = tower_new_credits_observed.max(ag_new_credits_observed);
    Ok((tower_points, ag_points, new_credits_observed))
}
```
