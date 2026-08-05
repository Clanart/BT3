## Analysis

The reported pattern is: a reward-rate change resets a checkpoint variable (`lastReward`) to "now" *before* accrued-but-undistributed rewards computed against the old rate are paid out, so those rewards become permanently unclaimable.

The closest analog in this codebase is in the stake-reward point calculation, where the checkpoint is `Stake::credits_observed` and the "rate" is the vote account's cumulative `epoch_credits`.### Title
`credits_observed` checkpoint is silently rewound (discarding accrued, unredeemed points) when a vote account's cumulative credits decrease - ([File: runtime/src/inflation_rewards/points.rs])

### Summary
`calculate_stake_points_and_credits` compares a stake account's stored checkpoint, `stake.credits_observed`, against the delegated vote account's cumulative `credits`. If the vote account's credits are ever observed to be *lower* than the stake's checkpoint, the function does not attempt to redeem any of the points that had already accrued between the old checkpoint and the moment of comparison — it simply snaps (rewinds) the checkpoint forward to the new, lower value and returns zero points. This is the same broken invariant as the `RewardVault::setDailyRewardRate` bug: a checkpoint used to bound "what's already been paid" is reset without first paying out (or otherwise preserving) whatever had accrued under the old baseline.

### Finding Description
In `calculate_stake_points_and_credits`, the checkpoint comparison is: [1](#0-0) 

```
let credits_in_stake = stake.credits_observed;
let credits_in_vote = vote_state.credits;
match credits_in_vote.cmp(&credits_in_stake) {
    Ordering::Less => { ... }
```

When `credits_in_vote < credits_in_stake`, the code takes the branch that force-rewinds `new_credits_observed` to the vote account's *current* (lower) value: [2](#0-1) 

The accompanying comment explains the assumption: the vote account must have been "recreated," and it is deemed "acceptable" to let the stake begin earning again immediately, since it "must have passed the required warmed-up at least once in the past already." Crucially, this path returns `tower_points: 0, ag_points: 0` — any points that had accrued between the moment `credits_observed` was last recorded and now are discarded, not redeemed. This value flows directly into `calculate_stake_rewards` at `runtime/src/inflation_rewards/mod.rs:236-249,274-276`, which unconditionally treats `force_credits_update_with_skipped_reward = true` as "skip reward, but still advance the checkpoint," i.e. exactly the `setDailyRewardRate`-style "reset checkpoint without distributing" pattern: [3](#0-2) [4](#0-3) 

The corrupted value is `Stake::credits_observed` — it is advanced to `credits_in_vote` (a value smaller than what was previously observed) with no corresponding payout for the gap that existed before the drop was noticed. Existing guards do not stop this: `delay_commission_updates`/`snapshot_epoch_vote_accounts` only protect the *commission rate* used in payout, not the credits/points checkpoint itself, and there is no invariant elsewhere in `redeem_delegation_rewards` / `redeem_stake_rewards` that requires `credits_in_vote` to be monotonically non-decreasing before trusting it.

### Impact Explanation
The party harmed is not the vote-account controller but the **stake account owner(s) who delegated to that vote account**. If a vote account's on-chain `epoch_credits`/cumulative credits value is ever lower than what a delegated stake's `credits_observed` recorded (whether via account recreation, or any other path that produces a lower observed value than previously recorded), all reward points that had accrued for that delegator since their last redemption are silently zeroed out and can never be redeemed — the checkpoint has already moved past them. This is a genuine, unrecoverable loss of accrued-but-undistributed staking rewards for an unprivileged party (the delegator), matching the "fund loss due to non-claimable yield" impact class in the source report.

### Likelihood Explanation
Medium. It requires the specific condition `credits_in_vote < credits_in_stake` to occur for some stake delegation, which the code's own comments say is intended to model "vote account recreation." This is a legitimate, permissible lifecycle event (the current vote-account keypair holder closing and recreating the account) rather than a malicious injection by an outside attacker, and it silently and permanently strands any not-yet-redeemed reward points for every stake still delegated to that vote pubkey at the time of the observed drop — without requiring any special timing exploit by an attacker.

### Recommendation
Before rewinding `new_credits_observed` downward, the code should first redeem/credit whatever points had accrued against the *old* (higher) `credits_observed` baseline using the last known-good vote credits, analogous to calling `distributeRewards` before updating `lastReward`. At minimum, the calculation should refuse to silently discard the un-redeemed interval — either by tracking rewound accounts explicitly (so an operator/runtime can detect and separately account for the loss) or by never advancing the checkpoint past a value for which rewards were not paid.

### Proof of Concept
Conceptual trace through the code (based on the existing unit test at `runtime/src/inflation_rewards/mod.rs:1019-1043`, which explicitly exercises and asserts this exact behavior):
1. A stake delegates to vote account `V`; over several epochs its `credits_observed` is advanced to, say, `1000 * multiplier` while genuine points accrue that have not yet been redeemed (e.g., the redemption instruction/epoch-boundary hasn't run yet for the latest interval).
2. Vote account `V`'s on-chain credits are reset to a lower cumulative value (e.g., via closing and recreating the account at the same pubkey, or any other legitimate mechanism that produces `vote_state.credits < stake.credits_observed`).
3. `calculate_stake_points_and_credits` is invoked (e.g. during epoch-boundary reward calculation) and takes the `Ordering::Less` branch: [5](#0-4) 
   The test shows `new_credits_observed` set to the vote account's low value with `force_credits_update_with_skipped_reward: true` and zero points — proving the accrued interval prior to the reset is permanently lost, with no code path to later recover or pay it out.

**Note on confidence**: I was unable to fully verify whether there exists a real, unprivileged (non-owner-controlled) path that produces `vote_state.credits < stake.credits_observed` outside of intentional vote-account recreation by its own controlling keypair — this would strengthen or weaken the "likelihood" assessment. Given index limits, I could not exhaustively trace all vote-account creation/closing code paths (e.g., `system_processor`, `vote_processor::initialize_account`) to confirm whether credits can regress through any other mechanism. A Devin session with full repository access would be needed to fully confirm all trigger paths for `vote_state.credits` regression.

### Citations

**File:** runtime/src/inflation_rewards/points.rs (L366-373)
```rust
    let credits_in_stake = stake.credits_observed;
    let credits_in_vote = vote_state.credits;
    // if there is no newer credits since observed, return no point
    match credits_in_vote.cmp(&credits_in_stake) {
        Ordering::Less => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnRewound.into());
            }
```

**File:** runtime/src/inflation_rewards/points.rs (L374-397)
```rust
            // Don't adjust stake.activation_epoch for simplicity:
            //  - generally fast-forwarding stake.activation_epoch forcibly (for
            //    artificial re-activation with re-warm-up) skews the stake
            //    history sysvar. And properly handling all the cases
            //    regarding deactivation epoch/warm-up/cool-down without
            //    introducing incentive skew is hard.
            //  - Conceptually, it should be acceptable for the staked SOLs at
            //    the recreated vote to receive rewards again immediately after
            //    rewind even if it looks like instant activation. That's
            //    because it must have passed the required warmed-up at least
            //    once in the past already
            //  - Also such a stake account remains to be a part of overall
            //    effective stake calculation even while the vote account is
            //    missing for (indefinite) time or remains to be pre-remove
            //    credits score. It should be treated equally to staking with
            //    delinquent validator with no differentiation.

            // hint with true to indicate some exceptional credits handling is needed
            return CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: credits_in_vote,
                force_credits_update_with_skipped_reward: true,
            };
```

**File:** runtime/src/inflation_rewards/mod.rs (L236-249)
```rust
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
```

**File:** runtime/src/inflation_rewards/mod.rs (L274-276)
```rust
    if force_credits_update_with_skipped_reward {
        return skipped_reward();
    }
```

**File:** runtime/src/inflation_rewards/mod.rs (L1019-1043)
```rust
        // credits_observed is auto-rewound when vote_state credits are assumed to have been
        // recreated
        stake.credits_observed = 1000 * ag_total_stake_multiplier;
        // this is new behavior 1; return the post-recreation rewound credits from the vote account
        assert_eq!(
            CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: 4 * ag_total_stake_multiplier,
                force_credits_update_with_skipped_reward: true,
            },
            calculate_stake_points_and_credits(
                &stake,
                DelegatedVoteState::from(vote_state.as_ref_v4()),
                &StakeHistory::default(),
                null_tracer(),
                None,
                &make_ag_epoch_type_for_test(
                    ag_enabled,
                    vote_state.as_ref_v4(),
                    ag_total_stake_multiplier
                ),
                true,
            )
        );
```
