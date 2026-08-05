[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** runtime/src/inflation_rewards/points.rs (L368-398)
```rust
    // if there is no newer credits since observed, return no point
    match credits_in_vote.cmp(&credits_in_stake) {
        Ordering::Less => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnRewound.into());
            }
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
        }
```

**File:** programs/vote/src/vote_state/handler.rs (L425-455)
```rust
    pub fn increment_credits(&mut self, epoch: Epoch, credits: u64) {
        // increment credits, record by epoch

        // never seen a credit
        if self.epoch_credits().is_empty() {
            self.epoch_credits_mut().push((epoch, 0, 0));
        } else if epoch != self.epoch_credits().last().unwrap().0 {
            let (_, credits, prev_credits) = *self.epoch_credits().last().unwrap();

            if credits != prev_credits {
                // if credits were earned previous epoch
                // append entry at end of list for the new epoch
                self.epoch_credits_mut().push((epoch, credits, credits));
            } else {
                // else just move the current epoch
                self.epoch_credits_mut().last_mut().unwrap().0 = epoch;
            }

            // Remove too old epoch_credits
            if self.epoch_credits().len() > MAX_EPOCH_CREDITS_HISTORY {
                self.epoch_credits_mut().remove(0);
            }
        }

        self.epoch_credits_mut().last_mut().unwrap().1 = self
            .epoch_credits()
            .last()
            .unwrap()
            .1
            .saturating_add(credits);
    }
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L534-598)
```rust
fn increment_credits(
    epoch_credits: &mut Vec<(Epoch, u64, u64)>,
    migration_epoch: Epoch,
    epoch: Epoch,
    new_credits: NonZero<u64>,
) {
    if epoch == migration_epoch {
        ensure_marker(epoch_credits);
    }

    let Some(entry) = epoch_credits.last_mut() else {
        // no entries, insert a new entry and we are done.
        epoch_credits.push((epoch, new_credits.get(), 0));
        return;
    };

    // Latest element is the marker, start a new entry.
    if *entry == AG_MIGRATION_EPOCH_CREDIT {
        // If there was a tower entry before, its final credits forms this entry's initial credits.
        let len = epoch_credits.len();
        let final_tower_credits = if len >= 2 {
            assert_ne!(epoch_credits[len - 2], AG_MIGRATION_EPOCH_CREDIT);
            epoch_credits[len - 2].1
        } else {
            0
        };
        epoch_credits.push((
            epoch,
            new_credits.get().saturating_add(final_tower_credits),
            final_tower_credits,
        ));
        while epoch_credits.len() > MAX_EPOCH_CREDITS_HISTORY {
            epoch_credits.remove(0);
        }
        return;
    }

    let (entry_epoch, final_credits, initial_credits) = entry;

    // Latest element is the same epoch, simply increment final credits.
    if *entry_epoch == epoch {
        *final_credits = final_credits.saturating_add(new_credits.get());
        return;
    }

    // Different epochs but the latest epoch didn't earn any credits, reuse the entry.
    if final_credits == initial_credits {
        *entry_epoch = epoch;
        *final_credits = final_credits.saturating_add(new_credits.get());
        return;
    }

    // Different epochs and the latest epoch earned credits, insert a new entry.
    let entry = (
        epoch,
        new_credits.get().saturating_add(*final_credits),
        *final_credits,
    );
    epoch_credits.push(entry);

    // maybe included a marker and a new entry above.  So might have multiple entries to remove here.
    while epoch_credits.len() > MAX_EPOCH_CREDITS_HISTORY {
        epoch_credits.remove(0);
    }
}
```
