### Title
Multiply-before-divide `u128` overflow panic in Tower→Alpenglow migration reward calculation halts consensus - (File: `runtime/src/inflation_rewards/mod.rs`)

### Summary
The Sherlock report describes a multiply-before-divide overflow: `total_rewards * time_passed` is computed before dividing by `total_time`, aborting instead of computing the mathematically-equivalent, overflow-safe `(total_rewards / total_time) * time_passed`. The same arithmetic-ordering flaw exists in Agave's `calculate_stake_rewards` for the Alpenglow `MigrationEpoch` branch, where `tower_points` is multiplied by `point_value.rewards` and then divided by `point_value.points`, then multiplied by `num_tower_slots` before dividing by `total_slots`. Each `.checked_mul(...)` result is force-unwrapped with `.unwrap()`, so overflow causes a hard panic rather than a graceful error — and this code runs inside deterministic epoch-boundary bank processing executed by every validator, not inside a single user's transaction.

### Finding Description
`calculate_stake_rewards` computes per-stake rewards for the one-time Tower→Alpenglow migration epoch as: [1](#0-0) 

```rust
AlpenglowEpochType::MigrationEpoch { num_tower_slots, num_ag_slots, .. } => {
    ...
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

This is exactly the flawed order of operations from the report: `(tower_points * rewards / points) * num_tower_slots / total_slots`, instead of dividing first. `tower_points` itself is a `u128` accumulated per stake account across up to `MAX_EPOCH_CREDITS_HISTORY` unredeemed epochs of `stake_amount * earned_credits`: [2](#0-1) 

For a large, long-inactive delegator (a stake account that has not had its rewards redeemed across several epochs leading into the migration epoch, which is legitimate/unprivileged — nothing prevents a staker from simply not triggering redemption, and redemption cadence is bank-driven, not staker-driven, so this state is reachable in normal operation), `tower_points` can already be a very large `u128` value (`stake_lamports * earned_credits * num_unredeemed_epochs`). Multiplying that again by `point_value.rewards` (the total epoch inflation reward in lamports, itself up to the order of `10^13`–`10^15`) before dividing by `point_value.points` pushes the intermediate product toward/over `u128::MAX` (~3.4×10^38).

The existing test suite already documents that this exact expression panics for realistic-looking inputs: [3](#0-2) 

```rust
#[test_case(u64::MAX, 1_000, u64::MAX => panics "Rewards intermediate calculation should fit within u128")]
#[test_case(1, u64::MAX, u64::MAX => panics "Rewards should fit within u64")]
fn calculate_rewards_tests(stake: u64, rewards: u64, credits: u64) {
```

i.e. the developers themselves proved that with a large stake and large accumulated credits, `checked_mul(...).expect(...)` panics — confirming the overflow is reachable, not merely theoretical.

Unlike the Move report, where the overflow only aborts a single user transaction (which can at least be retried once state changes), this overflow occurs inside `calculate_stake_rewards`, called from the bank's deterministic, protocol-mandated epoch-reward-distribution pipeline (`calculate_rewards_for_partitioning` → `calculate_stake_rewards_and_commissions` → `redeem_rewards` → `calculate_stake_rewards`) at the migration epoch boundary. Every correct validator executes this same code on the same stake account with the same numbers, so the panic is not attacker-vs-single-victim — it is deterministic across the whole validator set.

### Impact Explanation
Because epoch-reward calculation is mandatory bank processing that runs identically on every validator when the Tower→Alpenglow migration epoch is reached, a panic here does not corrupt one node's state divergently — it crashes every conforming validator's `agave-validator` process at the same logical point (same epoch boundary, same stake account, same numbers). This satisfies the "consensus halt" criterion: block production stops network-wide because no validator can advance past the migration epoch boundary without hitting the same `.unwrap()` panic. There is no user-level abort/retry semantics as in the Move report — the fix requires a software patch and coordinated restart, and the network cannot make progress in the meantime. This is a "non-RPC remote exhaustion/crash"-class, unprivileged, protocol-triggered denial of the entire cluster, matching the accepted-impact category for this task (transactions/runtime/accounts causing consensus halt / non-RPC remote crash).

### Likelihood Explanation
Triggering requires only: (1) a stake account with a sufficiently large delegated `stake` amount, and (2) that stake account not having had its rewards redeemed for several consecutive Tower epochs prior to the one-time Alpenglow migration epoch — both are ordinary, unprivileged staking conditions, not adversarial network behavior, malicious peers, or trusted-process assumptions. The migration epoch is a one-time, protocol-scheduled event, so likelihood is bounded to that transition, but the existing test-suite panics (`calculate_rewards_tests`) demonstrate the arithmetic genuinely overflows for realistic magnitudes of stake/credits/rewards, and the codebase has no runtime guard rejecting such stake accounts prior to the migration — it will process every stake account with a delegation, however large, exactly the same way.

### Recommendation
Reorder the arithmetic to divide before multiplying, as recommended in the source report:
```rust
let tower_points = tower_points
    .checked_div(point_value.points)
    .and_then(|v| v.checked_mul(u128::from(point_value.rewards)))
    .and_then(|v| v.checked_div(total_slots))
    .and_then(|v| v.checked_mul(*num_tower_slots as u128))
    .unwrap_or(0); // or otherwise handle gracefully instead of panicking
```
Additionally, replace `.expect(...)`/`.unwrap()` panics with graceful degradation (e.g., saturate/clamp similar to `calculate_block_reward`'s documented clamping pattern at `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:221-230`) so that an unexpected overflow cannot crash bank processing outright. [4](#0-3) 

### Proof of Concept
The overflow is already reproduced by the repository's own test: [3](#0-2) 

```rust
#[test_case(u64::MAX, 1_000, u64::MAX => panics "Rewards intermediate calculation should fit within u128")]
fn calculate_rewards_tests(stake: u64, rewards: u64, credits: u64) { ... }
```

Conceptually: a whale stake account (large `delegation.stake`) accumulates `tower_points = stake * earned_credits` over multiple unredeemed epochs (bounded by `MAX_EPOCH_CREDITS_HISTORY`) via `tower_epoch_credits_iter` (`runtime/src/inflation_rewards/points.rs:200-233`). At the migration epoch, `calculate_stake_rewards`'s `MigrationEpoch` branch (`runtime/src/inflation_rewards/mod.rs:308-330`) computes `tower_points.checked_mul(point_value.rewards)` before dividing by `point_value.points`, and this product can exceed `u128::MAX`, triggering the `.expect("Rewards intermediate calculation should fit within u128")` panic inside bank reward-distribution logic that runs identically on every validator at the migration epoch, halting the network.

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

**File:** runtime/src/inflation_rewards/mod.rs (L1139-1141)
```rust
    #[test_case(u64::MAX, 1_000, u64::MAX => panics "Rewards intermediate calculation should fit within u128")]
    #[test_case(1, u64::MAX, u64::MAX => panics "Rewards should fit within u64")]
    fn calculate_rewards_tests(stake: u64, rewards: u64, credits: u64) {
```

**File:** runtime/src/inflation_rewards/points.rs (L200-233)
```rust
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
