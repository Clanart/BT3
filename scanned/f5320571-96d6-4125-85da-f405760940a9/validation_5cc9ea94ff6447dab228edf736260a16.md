## Title
Division-by-zero panic in Alpenglow migration-epoch reward calculation - (File: `runtime/src/inflation_rewards/mod.rs`)

## Summary
`calculate_stake_rewards` in the `MigrationEpoch` branch guards against a zero `point_value.points` denominator only when `ag_points == 0`, but then unconditionally divides `tower_points` by `point_value.points`. If `tower_points != 0` while `point_value.points == 0`, the guard is bypassed and the code panics on division by zero — structurally the same class of bug as the C4 "Migration fails when all tokens are joined" finding, where a denominator-zero guard was written for the wrong condition.

## Finding Description
The reward-splitting logic for the Tower→Alpenglow migration epoch is: [1](#0-0) 

```rust
AlpenglowEpochType::MigrationEpoch { num_tower_slots, num_ag_slots, .. } => {
    if tower_points == 0 && ag_points == 0 {
        return skip_reward(SkippedReason::ZeroPoints);
    }
    if ag_points == 0 && point_value.points == 0 {
        return skip_reward(SkippedReason::ZeroPointValue);
    }
    let total_slots = (num_tower_slots + num_ag_slots) as u128;
    let tower_points = tower_points
        .checked_mul(u128::from(point_value.rewards))
        .expect("Rewards intermediate calculation should fit within u128")
        .checked_div(point_value.points)
        .unwrap()
        .checked_mul(*num_tower_slots as u128)
        .unwrap()
        .checked_div(total_slots)
        .unwrap();
    tower_points + ag_points
}
```

The two guard checks are:
1. `tower_points == 0 && ag_points == 0` — skip only if both are zero.
2. `ag_points == 0 && point_value.points == 0` — skip only if `ag_points` is zero **and** `point_value.points` is zero.

Neither check covers the case `tower_points != 0`, `ag_points != 0`, and `point_value.points == 0`. In that state, execution falls through to `.checked_div(point_value.points).unwrap()`, which is a division by zero on the denominator `point_value.points` — `checked_div` returns `None` for a zero divisor and the subsequent `.unwrap()` panics.

This mirrors the original report's root cause exactly: a special-case guard was written to reject "all tokens joined" / "zero total" scenarios, but the guard's boolean condition doesn't actually cover every combination of inputs that make the denominator zero — leaving a live division-by-zero path.

## Impact Explanation
`calculate_stake_rewards` is invoked as part of deterministic bank reward processing at epoch boundaries. If this code path panics, it does so identically on every validator replaying the same epoch-boundary state (since the inputs — `tower_points`, `ag_points`, `point_value` — are derived deterministically from on-chain stake/vote state), which would crash bank processing across the entire fleet simultaneously at the migration-epoch reward distribution boundary. This falls under the "consensus halt" / "false execution/rooting/acceptance" impact category defined in scope, since a synchronized panic in the runtime's reward-crediting path stalls or crashes the validator set at that boundary.

## Likelihood Explanation
This is scoped strictly to the one-time Tower→Alpenglow `MigrationEpoch`, so it can only be triggered during that specific transition window, and requires no malicious actor — it is a pure state-driven arithmetic bug (unprivileged, no attacker/trusted-role assumption). The likelihood of `point_value.points == 0` while an individual delegator's `tower_points != 0` depends on how the epoch-wide `point_value.points` total is aggregated relative to per-stake `tower_points`; I was not able to fully trace, within the available indexed code, the exact call site that constructs `point_value` for the `MigrationEpoch` branch to confirm whether the two values can diverge in practice (e.g., due to differing point calculation logic, rounding, or a stake being excluded from the aggregate but still individually computing `tower_points`). This should be verified against the full source (the `points.rs` aggregation logic and its callers) since the local index may not contain all relevant files.

## Recommendation
Change the second guard to unconditionally cover the divisor used in the `tower_points` computation, independent of `ag_points`:
```rust
if tower_points != 0 && point_value.points == 0 {
    return skip_reward(SkippedReason::ZeroPointValue);
}
```
or more robustly, replace `.checked_div(point_value.points).unwrap()` with a `Option`-returning path that falls back to `skip_reward(SkippedReason::ZeroPointValue)` instead of panicking, so any latent zero-denominator case fails safely rather than crashing the runtime.

## Proof of Concept
Conceptually (cannot be executed without the full test harness, which was not fully available in the index):
1. Construct a `Stake` and vote-state history such that `calculate_stake_points_and_credits` yields `tower_points > 0` and `ag_points > 0` for a given stake (both nonzero, e.g., stake has both tower epoch credits and AG epoch credits recorded).
2. Construct/obtain a `CalculationEnvironment.point_value` for this reward pass with `points == 0` (representing an epoch where, for whatever aggregation reason, the total tower `point_value.points` is computed as zero) while `rewards` is nonzero.
3. Call `calculate_stake_rewards` with `ag_epoch_type = AlpenglowEpochType::MigrationEpoch { .. }`.
4. Execution reaches: [2](#0-1) 
   and panics on `.checked_div(point_value.points).unwrap()` because `point_value.points == 0`, since neither guard at lines 313 and 316 catches this combination. [3](#0-2)

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L308-331)
```rust
        AlpenglowEpochType::MigrationEpoch {
            num_tower_slots,
            num_ag_slots,
            ..
        } => {
            if tower_points == 0 && ag_points == 0 {
                return skip_reward(SkippedReason::ZeroPoints);
            }
            if ag_points == 0 && point_value.points == 0 {
                return skip_reward(SkippedReason::ZeroPointValue);
            }
            let total_slots = (num_tower_slots + num_ag_slots) as u128;
            let tower_points = tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
                .checked_mul(*num_tower_slots as u128)
                .unwrap()
                .checked_div(total_slots)
                .unwrap();
            tower_points + ag_points
        }
    };
```
