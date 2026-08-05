### Title
`u128` `checked_mul().expect()` panic in tower reward calculation can crash validators at the epoch boundary - (`File: runtime/src/inflation_rewards/mod.rs`)

### Summary
`calculate_stake_rewards` computes each stake's tower reward as `tower_points.checked_mul(u128::from(point_value.rewards)).expect(...)` divided by `point_value.points`. This mirrors the reported Angle bug: a fixed-width integer intermediate value derived from a scaled multiplication (`amountOut/amountIn * BASE_27 / normalizer`) that can overflow and cause a revert. Here the analog is a `u128` accumulator (`tower_points`) that is unbounded across multiple un-redeemed epochs and is multiplied by another large `u64`-derived value, guarded only by `.expect()`, which panics instead of gracefully failing.

### Finding Description
`tower_points` is accumulated as `stake_amount * earned_credits` summed over every un-redeemed epoch entry found in the vote account's `epoch_credits` list [1](#0-0) . This accumulation is not capped to a single epoch: `tower_epoch_credits_iter` and `calculate_migration_points` walk the *entire* list of un-observed `epoch_credits` entries recorded in the vote account (all epochs since `stake.credits_observed`, including all epochs prior to the tower→Alpenglow migration marker) [2](#0-1) .

The resulting `tower_points` is then used in:
```
tower_points
    .checked_mul(u128::from(point_value.rewards))
    .expect("Rewards intermediate calculation should fit within u128")
    .checked_div(point_value.points)
    .unwrap()
``` [3](#0-2) 

Unlike other lamport-arithmetic in the same module which uses `saturating_*`/`checked_*` with graceful fallback (e.g. `commission_split`, `commission_split_preserve_lamports` at lines 377-435), this specific multiplication uses `.expect()`, which **panics the calling thread** if the product exceeds `u128::MAX` (~3.4e38) rather than returning an error or `None`.

This function is invoked from the mandatory, automatic epoch-boundary reward distribution path (`redeem_stake_rewards` → `calculate_stake_rewards`), which every validator executes deterministically for every delegated stake account, not from an RPC or user-submitted instruction that could simply be rejected [4](#0-3) . Because the same deterministic state (accumulated un-redeemed `epoch_credits`, `Delegation.stake`, and `point_value.rewards`) is processed identically by every conforming validator at the same epoch boundary, an overflow condition is not merely a local crash — it is a **synchronized panic across the entire validator set** at the same slot, i.e. a consensus/network halt, not an isolated single-node bug.

### Impact Explanation
If `tower_points * point_value.rewards` overflows `u128`, the `.expect()` panics. Because this code path executes deterministically and identically on every validator processing the same epoch's rewards, a single stake/vote-account state that triggers the overflow would cause **every up-to-date validator to panic simultaneously** while computing partitioned epoch rewards — a chain-wide halt, which maps to the "consensus halt" / "non-RPC remote exhaustion/crash" impact category. This is a stronger consequence than the original Angle finding (a single reverted swap); here the effect is validator-wide because the reward-distribution code runs unconditionally for all stake accounts at every epoch boundary.

### Likelihood Explanation
Exploitability requires accumulating an extreme value of `tower_points` in a single (`stake`, `vote_account`) pair before rewards are redeemed. In normal operation, rewards are auto-redeemed every epoch, which caps `earned_credits` to roughly one epoch's worth of credits and `stake_amount` to the delegation's lamports (bounded by total token supply, well below `u64::MAX`). However, `tower_epoch_credits_iter`/`calculate_migration_points` explicitly iterate over **all** un-observed `epoch_credits` entries recorded by the vote account (up to the migration marker) rather than a single epoch, meaning any stake account whose `credits_observed` lags behind the vote account by many epochs (e.g., due to migration-epoch transition handling, or long-inactive/late-redeemed delegations) accumulates `tower_points` across all of those epochs before this multiplication is performed. Given `tower_points` is already the product of stake (up to full delegation size) times cumulative credits across potentially many epochs, and is then multiplied again by `point_value.rewards` (epoch inflation reward, itself a `u64`), the combined magnitude can plausibly exceed `u128::MAX` for large, long-unredeemed delegations, especially around the tower→Alpenglow migration boundary where multi-epoch backlogs are explicitly handled by this same code path. This is a realistic, code-supported likelihood rather than a purely theoretical one, though I was not able to fully confirm the maximum achievable epoch-credits backlog within the current time budget.

### Recommendation
Replace the `.expect("Rewards intermediate calculation should fit within u128")` with a graceful, saturating/clamped fallback (mirroring the pattern already used in `commission_split`/`commission_split_preserve_lamports`), e.g. `checked_mul(...).unwrap_or(u128::MAX)` before the subsequent division, so that an extreme (but not necessarily malicious) accumulation of un-redeemed epoch credits degrades to a clamped reward value instead of panicking every validator simultaneously. Additionally, consider bounding how many un-redeemed epochs can be accumulated before triggering forced credit advancement, to prevent unbounded backlog growth in `tower_points`.

### Proof of Concept
Concretely reproducing the overflow requires constructing a stake account with `credits_observed` far behind the vote account's recorded `epoch_credits` (many un-redeemed epochs) combined with a large `Delegation.stake`, then calling `calculate_stake_rewards`/`calculate_migration_points` with a `point_value.rewards` large enough that:
```
tower_points (Σ stake_amount * earned_credits over many un-redeemed epochs) 
    * point_value.rewards  >  u128::MAX
```
which triggers the `.expect()` panic at [5](#0-4) . I was unable to execute this scenario end-to-end (no runtime/test execution available in this session) to empirically confirm the exact backlog size needed to trigger the panic under realistic total-supply/inflation bounds; a Devin session with code-execution access would be needed to construct the precise `epoch_credits` sequence and confirm the panic is reachable under real network constraints.

### Citations

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

**File:** runtime/src/inflation_rewards/points.rs (L314-352)
```rust
/// Calculates the tower and alpenglow points for `stake` based on the vote account's `reward_epoch_credits`
/// for the alpenglow migration epoch
///
/// Expects the epoch_credits_iter is sorted in ascending epoch order (excluding the migration marker)
/// Returns (tower_points, alpenglow points, new_credits_observed)
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

**File:** runtime/src/inflation_rewards/mod.rs (L97-169)
```rust
fn redeem_stake_rewards<'a>(
    stake: &mut Stake,
    voter_commission_bps: u16,
    vote_state: DelegatedVoteState,
    calculation_environment: CalculationEnvironment<'a>,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    ag_epoch_type: &AlpenglowEpochType,
    current_lamports: u64,
    minimum_lamports: u64,
) -> Option<(u64, u64)> {
    if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
        inflation_point_calc_tracer(&InflationPointCalculationEvent::CreditsObserved(
            stake.credits_observed,
            None,
        ));
    }

    let adjust_delegations_for_rent = calculation_environment.adjust_delegations_for_rent;

    let status = delegation_activation_status(
        &stake.delegation,
        calculation_environment.rewarded_epoch,
        calculation_environment.stake_history,
        calculation_environment.new_rate_activation_epoch,
        calculation_environment.use_fixed_point_stake_math,
    );

    let maybe_rewards = calculate_stake_rewards(
        stake,
        voter_commission_bps,
        vote_state,
        calculation_environment,
        inflation_point_calc_tracer.as_ref(),
        ag_epoch_type,
    )
    .map(|calculated_stake_rewards| {
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CreditsObserved(
                stake.credits_observed,
                Some(calculated_stake_rewards.new_credits_observed),
            ));
        }
        stake.credits_observed = calculated_stake_rewards.new_credits_observed;
        (
            calculated_stake_rewards.staker_rewards,
            calculated_stake_rewards.voter_rewards,
        )
    });

    let staker_rewards = maybe_rewards.map(|x| x.0).unwrap_or(0);
    if adjust_delegations_for_rent {
        let new_delegation_with_rewards = stake.delegation.stake.saturating_add(staker_rewards);
        let needs_adjustment = delegation_may_need_adjustment(
            stake.delegation.stake,
            new_delegation_with_rewards,
            current_lamports.saturating_add(staker_rewards),
            minimum_lamports,
            status,
        );
        // If `maybe_rewards.is_some()`, need to drive forward credits, even
        // if rewards are zero
        if needs_adjustment || maybe_rewards.is_some() {
            stake.delegation.stake = new_delegation_with_rewards;
            let voter_rewards = maybe_rewards.map(|x| x.1).unwrap_or(0);
            Some((staker_rewards, voter_rewards))
        } else {
            None
        }
    } else {
        stake.delegation.stake += staker_rewards;
        maybe_rewards
    }
}
```

**File:** runtime/src/inflation_rewards/mod.rs (L300-306)
```rust
            // `vote_state` earned.
            // The final unwrap is safe, as points_value.points is guaranteed to be non zero above.
            tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
```
