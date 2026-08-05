## Title
Alpenglow per-epoch reward computed as `earned_credits * stake_amount / total_stake` is unclamped, letting a shrinking stake-weight denominator mint rewards beyond the vote account's actual epoch payout - (File: `runtime/src/inflation_rewards/points.rs`)

### Summary
The external report's bug class is a share-mint formula of the form `amount * total_shares / pool_value` that blows up as `pool_value` shrinks toward (but never reaches) zero, and resets discontinuously at exactly zero. Agave's Alpenglow stake-rewards path has the structurally identical formula, `earned_credits * stake_amount / total_stake`, computed in `calculate_alpenglow_points` [1](#0-0) , and the result is used directly as the lamport reward for that stake (no further scaling), per the comment in `calculate_stake_rewards`: "In alpenglow, `points` represents the actual reward that this `stake` earned" [2](#0-1) .

### Finding Description
`calculate_alpenglow_points` divides by `total_stake`, the vote account's total delegated stake recorded in `reward_epoch_delegated_stakes` for the reward epoch, guarded only against the exact-zero case via `.filter(|stake| *stake != 0)` [3](#0-2) . There is no guard against `total_stake` being merely small relative to `stake_amount` (the individual delegation's `delegation_effective_stake` at the rewarded epoch) [4](#0-3) .

The sibling reward path, `calculate_block_reward` (SIMD-0123 block rewards), uses the exact same `numerator * stake / total_active_stake` shape but explicitly documents and clamps this mismatch:
"During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`... individual rewards look greater than the pending rewards... we clamp it just to be safe" followed by `.min(pending_delegator_rewards)` [5](#0-4) .

`calculate_alpenglow_points` has no equivalent clamp against `earned_credits` (which the function's own doc comment says represents "the lamports paid to the vote account" for that epoch) [6](#0-5) . The resulting `ag_points` flows unmodified into `calculate_stake_rewards` as `rewards`, is converted via `u64::try_from(rewards).expect(...)`, and then split into `staker_rewards`/`voter_rewards` and paid out through `redeem_delegation_rewards` → `calculate_stake_rewards_and_commissions` [7](#0-6) [8](#0-7) .

`stake_amount > total_stake` for a given (`stake`, `epoch`) pair is not a hypothetical: it's the same known-and-handled edge case cited for `calculate_block_reward`, because `delegation_effective_stake` is computed from `stake_history` at the specific `rewarded_epoch`, while `reward_epoch_delegated_stakes.delegated_stakes` is a separately-computed snapshot that, per the comment in `calculate_validator_rewards`'s caller, "already includes updated stake activation values from after the new epoch calculation" [9](#0-8) . Any churn (activation/deactivation) between delegators of the same vote account across the epoch boundary — an ordinary, unprivileged staking operation, not requiring a malicious validator — can make the denominator (`total_stake` at the delegated-stake snapshot) small relative to the numerator's `stake_amount` (effective stake at the rewarded epoch), reproducing the report's "denominator approaches near-zero → output balloons" invariant break, with the payout unclamped against the vote account's actual epoch earnings.

### Impact Explanation
Because the computed `ag_points`/`rewards` is paid out as real lamports through the stake-rewards distribution pipeline without being capped by the amount the vote account actually earned that epoch, this can mint lamports into a stake account in excess of the protocol's fixed epoch inflation budget (`epoch_inflation_rewards`/`PointValue::rewards`) [10](#0-9) , i.e., fund creation beyond the intended, capitalization-bounded reward schedule for the affected staker(s) — the direct analog of "LP providers minting disproportionately high shares."

### Likelihood Explanation
This requires only unprivileged actions (normal stake delegation/deactivation timed around an epoch boundary) affecting a vote account's `reward_epoch_delegated_stakes` snapshot versus the effective stake at the rewarded epoch — the exact scenario already acknowledged as reachable in the sibling `calculate_block_reward` function's comments, but left unguarded in the Alpenglow points path.

### Recommendation
Clamp the per-stake `ag_points`/rewards result in `calculate_alpenglow_points` to the vote account's actual epoch-earned lamports (`earned_credits`, i.e., `pending_delegator_rewards`-equivalent), mirroring the `.min(pending_delegator_rewards)` clamp already applied in `calculate_block_reward`, and/or reject/renormalize when `stake_amount > total_stake` rather than allowing the ratio to exceed 1.

### Proof of Concept
Not runnable from static analysis alone; conceptually mirrors the existing regression test structure such as `test_changing_total_stake` [11](#0-10) , but with `reward_epoch_validator_stake` set smaller than `staker_delegation` (post-churn) to show `earned_points`/rewards exceeding `earned_credits`, demonstrating the unclamped payout versus `calculate_block_reward`'s clamped equivalent.

### Citations

**File:** runtime/src/inflation_rewards/points.rs (L236-241)
```rust
/// Calculate alpenglow points for `stake` based on the vote account's `reward_epoch_credits`
///
/// This value is the lamports paid to the vote account * `stake_amount` / `vote_account_stake`
/// `vote_account_stake` is fetched from the precomputed `reward_epoch_delegated_stakes` for the
/// reward epoch
///
```

**File:** runtime/src/inflation_rewards/points.rs (L272-278)
```rust
    let stake_amount = u128::from(delegation_effective_stake(
        &stake.delegation,
        epoch,
        stake_history,
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    ));
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

**File:** runtime/src/inflation_rewards/points.rs (L920-963)
```rust
    #[test]
    fn test_changing_total_stake() {
        let pubkey = Pubkey::new_unique();
        let staker_delegation = LAMPORTS_PER_SOL;
        let reward_epoch_validator_stake = staker_delegation * 5;
        let stake = Stake {
            delegation: Delegation {
                voter_pubkey: pubkey,
                stake: staker_delegation,
                activation_epoch: u64::MAX,
                deactivation_epoch: u64::MAX,
                ..Default::default()
            },
            credits_observed: 0,
        };

        let credits = 1235;
        let epoch_credits = vec![
            (0, credits, 0),
            AG_MIGRATION_EPOCH_CREDIT,
            (0, credits * 2, credits),
            (1, credits * 3, credits * 2),
            (2, credits * 4, credits * 3),
        ];
        let reward_epoch_delegated_stakes = RewardEpochDelegatedStakes {
            epoch: 2,
            delegated_stakes: [(pubkey, reward_epoch_validator_stake)]
                .into_iter()
                .collect(),
        };
        let (points, new_credits) = calculate_alpenglow_points(
            &stake,
            epoch_credits.into_iter().last(),
            &StakeHistory::default(),
            null_tracer(),
            None,
            true,
            &reward_epoch_delegated_stakes,
        )
        .unwrap();
        assert_eq!(new_credits, credits * 4);
        let expected_points = credits * staker_delegation / reward_epoch_validator_stake;
        assert_eq!(points, expected_points as u128);
    }
```

**File:** runtime/src/inflation_rewards/mod.rs (L278-285)
```rust
    let rewards = match ag_epoch_type {
        AlpenglowEpochType::Alpenglow { .. } => {
            if ag_points == 0 {
                return skip_reward(SkippedReason::ZeroPoints);
            }
            // In alpenglow, `points` represents the actual reward that this `stake` earned.
            ag_points
        }
```

**File:** runtime/src/inflation_rewards/mod.rs (L333-343)
```rust
    let rewards = u64::try_from(rewards).expect("Rewards should fit within u64");

    // don't bother trying to split if fractional lamports got truncated
    if rewards == 0 {
        return skip_reward(SkippedReason::ZeroReward);
    }
    let (voter_rewards, staker_rewards, is_split) = if is_tower_epoch {
        commission_split(voter_commission_bps, rewards)
    } else {
        commission_split_preserve_lamports(voter_commission_bps, rewards)
    };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L190-193)
```rust
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L221-231)
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
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-849)
```rust
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
                    let maybe_reward_record = self.redeem_delegation_rewards(
                        rewarded_epoch,
                        stake_pubkey,
                        stake_account,
                        &point_value,
                        stake_history,
                        &cached_vote_accounts,
                        reward_calc_tracer.as_ref(),
                        new_warmup_cooldown_rate_epoch,
                        delay_commission_updates,
                        commission_rate_in_basis_points,
                        adjust_delegations_for_rent,
                        ag_epoch_type,
                        custom_commission_collector,
                        use_fixed_point_stake_math,
                    );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L960-968)
```rust
            AlpenglowEpochType::Alpenglow { .. } => {
                // In alpenglow, we do not need to compute `PointValue::points` as the final
                // rewards are simply the total credits stored in the vote account.  We just need
                // to return a `Some` value with valid rewards.
                return Some(PointValue {
                    rewards: epoch_inflation_rewards,
                    points: 0,
                });
            }
```
