### Title
Alpenglow stake-reward redemption silently drops credits earned during skipped epochs, permanently reducing staker yield - (`runtime/src/inflation_rewards/points.rs`)

### Summary
In Tower-epoch reward redemption, Agave iterates through the *entire* history of a vote account's `epoch_credits` entries via `tower_epoch_credits_iter`, so a stake account that has not redeemed rewards for several epochs still gets credited for every epoch it missed. In Alpenglow-epoch reward redemption, however, `calculate_alpenglow_points` only inspects the **single latest** `epoch_credits` entry (`vote_state.epoch_credits_iter.last()`), and `calc_earned_credits` treats that one entry's `(initial_epoch_credits, final_epoch_credits)` as if it fully described everything the staker missed since `credits_observed`. This mirrors the Tapioca bug exactly: a value (`credits_observed`) that should only ever be fast-forwarded in lock-step with an accrual computation gets silently jumped ahead to the latest checkpoint, erasing the interval between the old checkpoint and the last "accrual" event that was never paid out. [1](#0-0) 

### Finding Description
`calculate_stake_points_and_credits` dispatches on `AlpenglowEpochType`. For `Tower`, it calls `tower_epoch_credits_iter`, which walks the full `epoch_credits_iter` and, for every epoch entry, calls `calc_earned_credits` to add up `final_epoch_credits - initial_epoch_credits` (or `final - credits_observed` for a partially observed epoch), so nothing between `stake.credits_observed` and the vote account's latest cumulative credits is lost even across multiple unredeemed epochs: [2](#0-1) 

For `Alpenglow`, only the last entry of `epoch_credits_iter` is passed to `calculate_alpenglow_points`: [3](#0-2) 

Inside `calculate_alpenglow_points`, `calc_earned_credits` is invoked with just that single entry's `initial_epoch_credits`/`final_epoch_credits`: [4](#0-3) 

`calc_earned_credits`'s first branch is the broken invariant:
```
let earned_credits = if credits_in_stake < initial_epoch_credits {
    // the staker observed the entire epoch
    final_epoch_credits - initial_epoch_credits
} ...
*new_credits_observed = (*new_credits_observed).max(final_epoch_credits);
``` [5](#0-4) 

If `stake.credits_observed` (the "last accrued" checkpoint) is older than `initial_epoch_credits` of the *single* latest epoch entry — which happens whenever the stake has gone multiple epochs without a matching Alpenglow reward redemption (e.g. it was delinquent, its vote account skipped several reward epochs, or it re-entered the Alpenglow reward-eligible set after a gap) — the function only pays out `final_epoch_credits - initial_epoch_credits` (the last epoch's delta) and then jumps `credits_observed` all the way to `final_epoch_credits`. Every credit earned between the old `credits_observed` and `initial_epoch_credits` (i.e., all the intermediate epochs that were skipped because only `.last()` is examined instead of the full `epoch_credits_iter`) is discarded forever: `credits_observed` is fast-forwarded past that gap, so a future redemption can never recover it. This is structurally identical to Tapioca's `updatePause(..., resetAccrueTimestamp=true)` jumping `lastAccrued` to `block.timestamp` without first calling `_accrue()`: a monitoring/checkpoint value is advanced to "now" without ever paying for the elapsed, legitimately-earned interval.

No guard in `calculate_alpenglow_points`, `calc_earned_credits`, or `calculate_stake_points_and_credits` reconstructs the missing intermediate epochs for the Alpenglow path — unlike Tower's `tower_epoch_credits_iter`, which is explicitly designed to replay every epoch entry and thus does not have this gap.

### Impact Explanation
This causes an under-computation of staking rewards (loss of yield) for any delegator whose stake redemption for the Alpenglow path was not kept in lock-step with every epoch's `epoch_credits` entry. Because `credits_observed` is unconditionally advanced to `final_epoch_credits` regardless of how large the un-accounted gap is, the lost rewards can never be recovered in a later epoch — this is a permanent, protocol-wide loss of inflation rewards for affected stakers, analogous to the "Impact: High" classification in the original Tapioca report (loss of yield for a whole unaccounted period).

### Likelihood Explanation
This is not attacker-triggered; it is a latent correctness bug in the runtime's automatic per-epoch reward computation, exercised whenever a stake account's `credits_observed` predates the `initial_epoch_credits` of the single most-recent `epoch_credits` entry under the Alpenglow reward path (e.g. after a validator/stake experiences delinquency or a gap in reward-eligible epochs before Alpenglow rewards resume). The precondition is state-dependent (multi-epoch gap between `credits_observed` and the latest recorded epoch entry) rather than adversarial, matching the report's "Low probability, high impact" pattern.

### Recommendation
In the Alpenglow reward path, iterate over the full `epoch_credits_iter` history the way `tower_epoch_credits_iter` does (or otherwise sum earned credits across every skipped epoch since `stake.credits_observed`) instead of only consulting `.last()`, so that `credits_observed` is only ever advanced by an amount that has actually been paid, mirroring the Tapioca fix of never letting a checkpoint jump forward without first accruing the intervening value.

### Proof of Concept
1. A stake account earns and redeems Alpenglow rewards, leaving `stake.credits_observed = C0` at the end of epoch `E`.
2. Its delegated validator is delinquent (or otherwise produces no `epoch_credits` update) for epochs `E+1..E+k`, then resumes voting in epoch `E+k+1`, pushing a single new `epoch_credits` entry `(E+k+1, final=C2, initial=C1)` where `C0 < C1 < C2` (i.e., `C1` already reflects credits legitimately earned in the gap epochs that should have been claimable).
3. When rewards are redeemed for epoch `E+k+1`, `calculate_alpenglow_points` passes only this last entry to `calc_earned_credits`.
4. Since `credits_in_stake (C0) < initial_epoch_credits (C1)`, the function returns `earned_credits = C2 - C1` (only the newest epoch's delta) and sets `new_credits_observed = C2`.
5. The delegator never receives rewards corresponding to `C1 - C0`, and because `credits_observed` is now `C2`, that gap can never be recovered in any future redemption — the yield for the interval `[C0, C1)` is permanently erased, identical in effect to the Tapioca `updatePause` bug. [5](#0-4) [6](#0-5)

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

**File:** runtime/src/inflation_rewards/points.rs (L251-270)
```rust
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
```

**File:** runtime/src/inflation_rewards/points.rs (L414-463)
```rust
    let (tower_points, ag_points, new_credits_observed) = match ag_epoch_type {
        AlpenglowEpochType::Tower => {
            let (points, credits, _) = tower_epoch_credits_iter(
                stake,
                vote_state.epoch_credits_iter,
                stake_history,
                inflation_point_calc_tracer,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            (points, 0, credits)
        }
        AlpenglowEpochType::MigrationEpoch {
            migration_epoch,
            reward_epoch_delegated_stakes,
            ..
        } => {
            debug_assert_eq!(reward_epoch_delegated_stakes.epoch, *migration_epoch);
            match calculate_migration_points(
                stake,
                vote_state.epoch_credits_iter,
                stake_history,
                inflation_point_calc_tracer,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
                reward_epoch_delegated_stakes,
            ) {
                Ok(r) => r,
                Err(e) => return e,
            }
        }
        AlpenglowEpochType::Alpenglow {
            migration_epoch,
            reward_epoch_delegated_stakes,
        } => {
            debug_assert!(reward_epoch_delegated_stakes.epoch > *migration_epoch);
            let (ag_points, credits) = match calculate_alpenglow_points(
                stake,
                vote_state.epoch_credits_iter.last(),
                stake_history,
                inflation_point_calc_tracer,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
                reward_epoch_delegated_stakes,
            ) {
                Ok(result) => result,
                Err(e) => return e,
            };
            (0, ag_points, credits)
        }
```
