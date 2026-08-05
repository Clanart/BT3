### Title
Block-revenue-sharing rewards distributed by end-of-epoch stake snapshot rather than time-weighted participation, allowing last-moment stake merges/delegations to claim a full epoch's `pending_delegator_rewards` — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The Velodrome `Gauge.sol` bug rewarded stakers based on a single overwritten "checkpoint" flag rather than tracking participation continuously, letting a user "vote" for one instant and collect a reward meant to be earned over an entire period. Agave's SIMD-0123 block-revenue-sharing mechanism has a structurally analogous "single snapshot" reward split: `calculate_block_reward` distributes an epoch's entire `pending_delegator_rewards` pool for a vote account proportionally to each stake delegation's `delegation_effective_stake` *at the rewarded epoch*, divided by a single end-of-epoch total-stake snapshot (`reward_epoch_delegated_stakes`), rather than integrating each delegation's actual participation over the period during which the block revenue accrued.

### Finding Description
`calculate_block_reward` in [1](#0-0)  computes:

```
total_active_stake = reward_epoch_delegated_stakes.delegated_stakes[vote_pubkey]  // single end-of-epoch snapshot
stake = delegation_effective_stake(delegation, rewarded_epoch, ...)               // single point-in-time value
block_reward = pending_delegator_rewards * stake / total_active_stake
```

Both the numerator (`stake`) and denominator (`total_active_stake`) are evaluated at one fixed point — the end of `rewarded_epoch` — via `reward_epoch_delegated_stakes`, which is populated once per epoch by `Stakes::calculate_activated_stake` and stored as a static snapshot (`compute_new_epoch_caches_and_rewards` in [2](#0-1) , and `reward_epoch_delegated_stakes.set(...)`).

This is distinct from the tower/inflation-reward path, which correctly tracks participation through `credits_observed` and integrates each per-epoch credit delta from the vote account's `epoch_credits` history (see `tower_epoch_credits_iter` / `calc_earned_credits` in [3](#0-2) , and [4](#0-3) ). For block-revenue sharing, however, there is no equivalent "observed-since-last-checkpoint" bookkeeping: the share for the whole epoch's accumulated `pending_delegator_rewards` (which the vote account collects incrementally, block-by-block, over the whole epoch) is computed purely from the stake's balance/effective-stake value recorded once at epoch's end, exactly mirroring the Gauge.sol pattern of overwriting a single checkpoint value rather than accruing per-interval state.

### Impact Explanation
If a delegation can increase its `delegation_effective_stake` value observed at `rewarded_epoch` without having actually been staked for the period during which the vote account earned `pending_delegator_rewards` (e.g., via a stake merge combining an already-active stake account into another right at/near the epoch boundary, or any operation that increases effective stake without incurring the warm-up delay applied to newly delegated stake), that delegation captures a share of block revenue disproportionate to its true time-weighted contribution. This effectively steals a portion of block-revenue rewards that should have accrued to delegations that were staked throughout the whole epoch, causing false reward allocation/fund loss for other stakers on the same vote account.

### Likelihood Explanation
This requires precise verification of the stake-merge/warm-up code path (whether merged/increased stake becomes "effective" instantly for the purposes of `delegation_effective_stake` at the current epoch boundary, bypassing the warm-up throttle that normally delays newly-delegated stake). I was not able to fully confirm this timing detail from the available indexed code (`programs/stake*` merge logic and `delegation_effective_stake` internals were not found in the index), so likelihood is **uncertain** and requires further investigation with full repository access. If merged/increased stake is treated as immediately effective at the snapshot instant, likelihood is moderate-to-high since any unprivileged staker can perform stake operations at self-chosen times.

### Recommendation
Since the exact timing semantics of `delegation_effective_stake` and stake merge/warm-up interaction could not be fully confirmed with the indexed subset of the codebase, recommend that a background engineer:
1. Inspect `runtime/src/stake_delegation.rs` (`delegation_effective_stake`) and the stake program's merge instruction handler to determine whether merged or newly-increased stake bypasses warm-up and is counted as fully effective at the very epoch in which `reward_epoch_delegated_stakes` is snapshotted.
2. If confirmed, change `calculate_block_reward` to weight each delegation's share of `pending_delegator_rewards` by time-integrated stake (e.g., minimum effective stake held throughout the epoch, or a lamport-seconds accumulation), analogous to how `calc_earned_credits`/`tower_epoch_credits_iter` integrate credits over the epoch's history rather than relying on a single end-of-period snapshot.
3. Add regression tests exercising a stake merge/increase immediately before the reward-epoch boundary to confirm block-reward payout is proportional only to genuinely time-weighted participation.

### Proof of Concept
Could not be fully constructed without access to the exact warm-up/merge semantics (see Likelihood Explanation). The suspected exploit path, pending verification, is:
1. Staker A holds a small active (already-warmed-up) stake delegated to vote account V. Vote account V accrues `pending_delegator_rewards` from block revenue over epoch N.
2. Near the end of epoch N (or at the point the `reward_epoch_delegated_stakes` snapshot for epoch N is captured in `compute_new_epoch_caches_and_rewards`), staker A merges a large already-active stake account into their delegation to V via the stake `merge` instruction (`cli/src/stake.rs` `process_merge_stake` / stake program `merge` instruction), instantly increasing `delegation_effective_stake` for the merged delegation.
3. When `calculate_block_reward` runs for epoch N (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:174-232`), staker A's now-large `stake` value is used as the numerator against the epoch-end `total_active_stake` snapshot, granting staker A a share of the entire epoch's `pending_delegator_rewards` despite only holding the large stake for a fraction of the epoch.
4. Staker A can subsequently split/withdraw the stake immediately after rewards are distributed. [1](#0-0) [2](#0-1)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
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
}
```

**File:** runtime/src/bank.rs (L1759-1790)
```rust
        // Add new entry to stakes.stake_history, set appropriate epoch and
        // update vote accounts with warmed up stakes before saving a
        // snapshot of stakes in epoch stakes
        let stakes = self.stakes_cache.stakes();
        let stake_delegations = stakes.stake_delegations_vec();
        let (
            (
                stake_history,
                unfiltered_distribution_vote_accounts,
                delegated_stakes,
                reward_epoch_delegated_stakes,
            ),
            calculate_activated_stake_time_us,
        ) = measure_us!(stakes.calculate_activated_stake(
            self.epoch(),
            thread_pool,
            self.new_warmup_cooldown_rate_epoch(),
            &stake_delegations,
            self.use_fixed_point_stake_math(),
        ));
        debug_assert_eq!(reward_epoch_delegated_stakes.epoch, rewarded_epoch);

        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
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
