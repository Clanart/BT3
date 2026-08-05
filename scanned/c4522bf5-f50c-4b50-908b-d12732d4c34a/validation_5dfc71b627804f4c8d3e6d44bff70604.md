## Title
Division-by-zero panic in migration-epoch reward calculation when `num_tower_slots + num_ag_slots == 0` - ([File: runtime/src/inflation_rewards/mod.rs])

## Summary
The Solidity report describes a division whose divisor (`timeSinceMigration`, i.e. `finalMigrationTime - lastClaimTime`) can be zero in an edge case (claim in the same block as migration), causing every subsequent call to revert and permanently stick the account. The Agave analog is `calculate_stake_rewards` in `runtime/src/inflation_rewards/mod.rs`, which computes a reward for the Alpenglow/Tower "migration epoch" by dividing by `total_slots = num_tower_slots + num_ag_slots` without ever checking that this sum is non-zero before calling `.checked_div(total_slots).unwrap()`.

## Finding Description
In the `AlpenglowEpochType::MigrationEpoch` branch of `calculate_stake_rewards`, the reward is derived as: [1](#0-0) 

The code guards against `tower_points == 0 && ag_points == 0` and against `ag_points == 0 && point_value.points == 0`, but it never guards the actual divisor used at line 327: `total_slots = (num_tower_slots + num_ag_slots) as u128`. The comment at line 301/324 ("the final unwrap is safe, as point_value.points is guaranteed non-zero") only justifies the *first* `checked_div(point_value.points)` — it does not extend to the second division by `total_slots`. If `tower_points != 0` (which passes the first guard) while `num_tower_slots` and `num_ag_slots` are both `0` — i.e., the slot-count denominator becomes decoupled from the points numerator due to how these two quantities are independently derived (one from vote-account epoch-credits history via `calculate_stake_points_and_credits`/`tower_epoch_credits_iter`, the other from the `AlpenglowEpochType::MigrationEpoch { num_tower_slots, num_ag_slots, .. }` struct populated elsewhere) — then `checked_div(total_slots)` returns `None`, and `.unwrap()` panics.

This is structurally identical to the reported bug: a value (`timeSinceMigration` / here `total_slots`) that is *assumed* to be non-zero whenever the numerator is non-zero, but whose zero case is never explicitly checked before being used as a divisor.

## Impact Explanation
This calculation runs during epoch-boundary reward distribution inside `runtime/src/inflation_rewards/mod.rs`, which is invoked from the partitioned epoch rewards path in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` — core validator runtime code executed by every validator identically during the migration epoch from Tower to Alpenglow consensus. A panic here on all validators simultaneously (since the epoch/points/slot-counts are derived deterministically from on-chain state) would cause a synchronized validator crash, i.e. a non-RPC crash / potential consensus halt during the migration epoch, which fits the accepted impact categories (false execution/rooting/acceptance, consensus halt).

## Likelihood Explanation
This is a one-time-epoch code path (migration epoch only) and I could not fully confirm from the index whether `num_tower_slots`/`num_ag_slots` can actually be zero while `tower_points` is non-zero given how `AlpenglowEpochType::MigrationEpoch` is constructed in `runtime/src/alpenglow_epoch_type.rs` — that file's exact construction logic was not fully retrieved before the tool budget ran out. The missing guard itself is clearly present in the code (no zero-check on `total_slots` before `.unwrap()`), but whether the numerator/denominator can genuinely desynchronize (rather than being provably coupled by invariant) is **unverified**. This uncertainty should be resolved by inspecting `runtime/src/alpenglow_epoch_type.rs` in full and the call sites that populate `num_tower_slots`/`num_ag_slots`.

## Recommendation
Explicitly check `total_slots == 0` before performing `checked_div(total_slots)` in `calculate_stake_rewards` (`runtime/src/inflation_rewards/mod.rs`, MigrationEpoch branch), returning a skipped/zero-reward result (consistent with the other guarded branches) instead of relying on `.unwrap()` to never panic. Additionally, audit `runtime/src/alpenglow_epoch_type.rs` to confirm/document the invariant that `num_tower_slots + num_ag_slots > 0` whenever `tower_points != 0`, and add a debug assertion or fuzz test exercising the migration-epoch boundary with degenerate slot counts.

## Proof of Concept
Not independently reproducible from the indexed code alone: exploiting this requires reaching `calculate_stake_rewards` with `ag_epoch_type = AlpenglowEpochType::MigrationEpoch { num_tower_slots: 0, num_ag_slots: 0, .. }` while `tower_points != 0`. I was unable to confirm within the available tool budget whether the surrounding code in `runtime/src/alpenglow_epoch_type.rs` structurally prevents this combination. A Devin session with full repository access is recommended to (1) read `runtime/src/alpenglow_epoch_type.rs` in full to determine how `num_tower_slots`/`num_ag_slots` are set, (2) trace all callers of `calculate_stake_rewards` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, and (3) construct a concrete Stake/VoteState/epoch-credits fixture that produces non-zero `tower_points` with zero `num_tower_slots + num_ag_slots`, to confirm the panic is reachable through legitimate epoch-boundary reward processing.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L308-330)
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
```
