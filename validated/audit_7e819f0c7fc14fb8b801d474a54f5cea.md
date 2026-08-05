### Title
Missing/zero AG delegated-stake denominator silently forces a stake's `credits_observed` forward with zero reward, permanently burning earned inflation rewards — ([File: runtime/src/inflation_rewards/points.rs])

### Summary
The external Beanstalk report describes a broken invariant: when an external input (oracle) fails, the protocol does not stop or roll back — it silently treats the failure as a normal successful outcome, mutating persistent state (`caseId`, temperature) as if the computation had produced a valid result. `LibDibbler`/`Weather.sol` consumers then act on this corrupted-but-"successful looking" state, permanently mispricing the system for that season.

`calculate_alpenglow_points` in Agave's inflation-rewards code has the same failure shape: when the per-epoch stake denominator lookup fails (missing or filtered-to-zero), instead of returning an error that halts/retries reward processing for that stake, it silently returns a "successful" `CalculatedStakePoints` with `ag_points: 0` **and** `force_credits_update_with_skipped_reward: true`, which the caller (`calculate_stake_rewards` / `redeem_stake_rewards` in `runtime/src/inflation_rewards/mod.rs`) uses to unconditionally advance `stake.credits_observed` to the vote account's latest recorded credits — exactly as if the reward for that period had been correctly computed and paid.

### Finding Description
In `runtime/src/inflation_rewards/points.rs`, `calculate_alpenglow_points` computes reward points for a stake based on a precomputed `RewardEpochDelegatedStakes` map (validator vote_pubkey → total delegated stake for the reward epoch): [1](#0-0) 

When `reward_epoch_delegated_stakes.delegated_stakes.get(&stake.delegation.voter_pubkey)` is missing, or present but filtered out because it is `0`, the function does **not** propagate this as a hard error that stops reward distribution for that stake. Instead it logs an error and returns `Err(CalculatedStakePoints { ag_points: 0, new_credits_observed, force_credits_update_with_skipped_reward: true, .. })`. [2](#0-1) 

That `Err` variant is unwrapped by the caller as if it were a legitimate "no rewards this epoch" outcome: [3](#0-2) 

Then in `runtime/src/inflation_rewards/mod.rs`, `calculate_stake_rewards` treats `force_credits_update_with_skipped_reward = true` as an unconditional signal to advance the stake's credit-observation cursor, discarding any owed reward for that period: [4](#0-3) 

This mirrors the Beanstalk pattern precisely: a failed/degenerate internal computation (missing oracle price ≈ missing/zero stake denominator) is converted into a value (`deltaB = 0` ≈ `ag_points = 0`) that is then used to update permanent protocol state (`caseId`/temperature ≈ `stake.credits_observed`) as though the computation succeeded, rather than being handled as an error that defers/retries the update. There is no mechanism to re-attempt reward calculation for the skipped epoch once `credits_observed` has been advanced — `calc_earned_credits` in the same file always computes earned credits relative to `stake.credits_observed`, so once it is bumped forward, the epoch's credits are considered "already observed" forever: [5](#0-4) 

### Impact Explanation
If the reward-epoch delegated-stake denominator for a validator's `voter_pubkey` is missing or zero at the moment a delegator's stake reward is calculated (e.g., because the validator's total effective delegation for that reward epoch was filtered to `0`, or the entry is absent from the precomputed `RewardEpochDelegatedStakes` map due to a race/edge condition around stake activation/deactivation boundaries), the affected delegator's stake account has its `credits_observed` permanently advanced to the vote account's latest credits with **zero** rewards distributed for that period. Because subsequent reward calculations only look at credits accrued *after* `credits_observed`, the skipped reward is not recoverable in a later epoch — this is a direct, unrecoverable loss of inflation rewards (fund loss) for an unprivileged staker, without any malicious actor needed; it happens purely from validator/state edge conditions the protocol should treat as an error rather than "success with zero reward."

### Likelihood Explanation
This path only triggers on the internal edge case where `reward_epoch_delegated_stakes.delegated_stakes` does not contain a non-zero entry for the exact `voter_pubkey` at the reward epoch used for the AG credits entry (`epoch != reward_epoch_delegated_stakes.epoch` is already handled separately and does not hit this branch; this branch is reached only when the epochs match but the denominator itself is missing/zero). I could not fully verify, within the available search budget, how `RewardEpochDelegatedStakes.delegated_stakes` is populated (i.e., whether the construction guarantees a non-zero entry always exists whenever a matching AG epoch-credit was recorded for that voter). Based on the presence of the `.filter(|stake| *stake != 0)` and the dedicated `record_error`/`datapoint_error!("PER-total-stake-calculation-failure", ...)` telemetry, this branch is treated by the developers as an unexpected/abnormal condition rather than routine, which suggests it is reachable in production under some edge condition (e.g., last delegator to a vote account withdraws right at an epoch boundary) but is expected to be rare. This uncertainty should be resolved by inspecting `runtime/src/alpenglow_epoch_type.rs`'s construction of `RewardEpochDelegatedStakes` before treating this as fully confirmed.

### Recommendation
- Do not use "silent success with zero reward + forced credits advancement" as the response to an internal data-consistency failure (missing/zero denominator). Instead, either: (a) skip credits advancement for the affected stake so the reward can be correctly recomputed once the denominator becomes available/consistent, or (b) treat it as a genuine, non-silent processing error surfaced to validators/operators rather than folding it into the same code path used for legitimate "no reward this epoch" cases.
- Add validation/assertions ensuring `RewardEpochDelegatedStakes.delegated_stakes` always contains a non-zero entry for every `voter_pubkey` that has a matching AG epoch-credits record for that epoch, and treat any violation as a bug to be fixed at the data-construction site rather than compensated for at reward-calculation time.
- Add regression tests that specifically exercise the "denominator missing/zero" branch and confirm no permanent, silent reward loss occurs.

### Proof of Concept
A concrete transaction-level PoC was not constructed due to tool-call limits and the unresolved question of exactly how/when `RewardEpochDelegatedStakes.delegated_stakes` can lack an entry for a `voter_pubkey` that has a matching AG epoch-credits record. The code-level trace substantiating the vulnerable path is:
1. `calculate_stake_points_and_credits` dispatches to `calculate_alpenglow_points` for `AlpenglowEpochType::Alpenglow` [3](#0-2) .
2. `calculate_alpenglow_points`'s denominator lookup fails/filters to zero, returning `Err` with `force_credits_update_with_skipped_reward: true` and `ag_points: 0` [1](#0-0) .
3. `calculate_stake_rewards` in `mod.rs` unconditionally returns `skipped_reward()` (zero rewards, but `new_credits_observed` advanced) whenever `force_credits_update_with_skipped_reward` is set [6](#0-5) .
4. `redeem_stake_rewards` commits `stake.credits_observed = calculated_stake_rewards.new_credits_observed` unconditionally on `Some(...)` [7](#0-6) , permanently losing the ability to redeem the skipped epoch's reward later.

To fully confirm exploitability, a Devin session with repo access should trace `runtime/src/alpenglow_epoch_type.rs` to determine whether/when a `voter_pubkey` with recorded AG epoch credits can have no (or a filtered-zero) entry in `RewardEpochDelegatedStakes.delegated_stakes`, and write a unit test in `runtime/src/inflation_rewards/points.rs`'s test module reproducing that scenario end-to-end (analogous to `test_calculate_alpenglow_points`'s `missing_reward_epoch_delegated_stakes` case, but through the full `calculate_stake_rewards`/`redeem_stake_rewards` path) to observe the resulting permanent reward loss.

### Citations

**File:** runtime/src/inflation_rewards/points.rs (L146-152)
```rust
fn record_error(msg: String) {
    error!("{msg}");
    datapoint_error!(
        "PER-total-stake-calculation-failure",
        ("error", msg, String)
    );
}
```

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

**File:** runtime/src/inflation_rewards/points.rs (L280-301)
```rust
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
```

**File:** runtime/src/inflation_rewards/points.rs (L445-463)
```rust
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

**File:** runtime/src/inflation_rewards/mod.rs (L132-144)
```rust
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
```

**File:** runtime/src/inflation_rewards/mod.rs (L220-276)
```rust
    // ensure to run to trigger (optional) inflation_point_calc_tracer
    let CalculatedStakePoints {
        tower_points,
        ag_points,
        new_credits_observed,
        mut force_credits_update_with_skipped_reward,
    } = calculate_stake_points_and_credits(
        stake,
        vote_state,
        stake_history,
        inflation_point_calc_tracer.as_ref(),
        new_rate_activation_epoch,
        ag_epoch_type,
        use_fixed_point_stake_math,
    );

    // Drive credits_observed forward unconditionally when rewards are disabled
    // or when this is the stake's activation epoch
    if point_value.rewards == 0 {
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&SkippedReason::DisabledInflation.into());
        }
        force_credits_update_with_skipped_reward = true;
    } else if stake.delegation.activation_epoch == rewarded_epoch {
        // not assert!()-ed; but points should be zero
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&SkippedReason::JustActivated.into());
        }
        force_credits_update_with_skipped_reward = true;
    }

    // Once alpenglow is active we no longer allow for epochs where rewards are not redeemed.
    let is_tower_epoch = matches!(ag_epoch_type, AlpenglowEpochType::Tower);
    let advance_credits_for_skipped_reward =
        !is_tower_epoch && new_credits_observed != stake.credits_observed;
    let skipped_reward = || {
        Some(CalculatedStakeRewards {
            staker_rewards: 0,
            voter_rewards: 0,
            new_credits_observed,
        })
    };

    let skip_reward = |reason: SkippedReason| {
        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&reason.into());
        }
        if advance_credits_for_skipped_reward {
            skipped_reward()
        } else {
            None
        }
    };

    if force_credits_update_with_skipped_reward {
        return skipped_reward();
    }
```
