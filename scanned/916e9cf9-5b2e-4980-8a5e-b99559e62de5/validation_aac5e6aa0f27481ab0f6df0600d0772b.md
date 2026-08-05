Based on my research, the closest structural analog to the Sherlock report exists in Agave's Alpenglow migration-epoch reward math, where the "same conceptual quantity" (a stake's tower-epoch reward share) is computed via a different order of divisions than the single-step proportional splits used elsewhere in the same code path, and the resulting sum is checked against the total by a hard `assert!` that will panic deterministically across all validators if violated.

### Title
Double-rounding in migration-epoch tower reward split can violate reward invariant and panic all validators - (File: runtime/src/inflation_rewards/mod.rs)

### Summary
`calculate_stake_rewards` computes a stake's migration-epoch tower reward using two sequential integer divisions (`points * rewards / total_points`, then `* num_tower_slots / total_slots`) rather than a single combined division, while the total "epoch inflation rewards" that this per-stake sum is later checked against in `distribute_reward_commissions` is derived from a single, differently-ordered calculation. This mirrors the Convergence bug: the same abstract quantity (a share of `total_points`/`total_slots`) is computed with different division orderings in two places, so the sum of individually-rounded shares is not guaranteed to be bounded consistently against the aggregate total.

### Finding Description
In the `MigrationEpoch` branch of `calculate_stake_rewards`, the tower portion of a stake's reward is computed as: [1](#0-0) 
i.e. `((tower_points * point_value.rewards) / point_value.points) * num_tower_slots / total_slots` — two chained divisions, each independently truncating.

This differs from the single-division form used for the pure `Tower` epoch case just above it: [2](#0-1) 

and from the alpenglow-points calculation, which uses one division (`earned_credits * stake_amount / total_stake`): [3](#0-2) 

Because `calculate_stake_rewards` for the `MigrationEpoch` branch truncates twice per stake account (once dividing by `point_value.points`, once by `total_slots`), the accumulated sum of these truncated per-stake payouts is not equal (and can, before the fix in `commission_split_preserve_lamports`, drift) from the single aggregate value that the code elsewhere assumes was already allocated for the tower portion of the migration epoch. The aggregate sum, `total_stake_rewards_lamports`, produced by summing all these per-stake `calculate_stake_rewards` outputs, is asserted against `point_value.rewards` (plus commissions/burns) in `distribute_reward_commissions`: [4](#0-3) 

This is a hard `assert!`, not a graceful error path — if the double-rounded per-stake sums combined with commission math ever exceed `point_value.rewards`, the assertion fails and panics.

### Impact Explanation
An `assert!` failure in bank reward distribution is deterministic across all validators processing the same epoch-boundary block (this code runs identically in every node's state transition, not a leader-only or RPC-only path). A panic here during a block that is part of the canonical chain would crash all following nodes at the same point, which is a consensus halt — every honest validator that processes this transition dies identically, since the computation is a pure function of on-chain state (stake amounts, credits, and epoch data) that all validators share. This falls squarely in the "false execution/rooting/acceptance, consensus halt" impact bucket.

### Likelihood Explanation
This is speculative and unverified against a concrete failing numeric scenario: I was not able to fully trace, within the available indexed code, whether `total_slots`, `num_tower_slots`, and `point_value.points`/`rewards` are chosen such that the double-division truncation can actually push `total_stake_rewards_lamports` above `point_value.rewards` given the `commission_split_preserve_lamports` remainder-preserving logic used in non-Tower epochs (migration epoch uses `is_tower_epoch = false`, hence `commission_split_preserve_lamports`, which explicitly assigns the fractional remainder to the voter so lamports are not lost per-stake). This remainder-preservation on the commission split likely absorbs local truncation per stake, which weakens (but does not conclusively rule out) the analog to the Convergence bug, since Convergence's bug lacked any such remainder-recapture mechanism. Whether the *global* sum across many stakes can still exceed `point_value.rewards` due to the two independent truncations (`/ point_value.points` then `/ total_slots`) was not confirmed with a concrete numeric trace in the code available to me.

### Recommendation
Combine the two divisions in the `MigrationEpoch` tower-points calculation into a single division (e.g., `tower_points * point_value.rewards * num_tower_slots / (point_value.points * total_slots)` using `u128` to avoid overflow) so that the rounding is bounded by a single truncation consistent with how `point_value.rewards` is apportioned across tower/alpenglow slot totals elsewhere, and verify with an explicit invariant test that `sum(per_stake_reward) <= point_value.rewards * num_tower_slots / total_slots` cannot be violated, rather than relying solely on the downstream `assert!` in `distribute_reward_commissions`.

### Proof of Concept
Not independently confirmed with a concrete failing numeric trace — the indexed code shows the structural double-division mismatch at [1](#0-0)  and the hard invariant assertion at [4](#0-3) , but I could not fully trace, within the tool budget available, whether `commission_split_preserve_lamports`'s remainder-recapture (at [5](#0-4) ) prevents the aggregate overflow across many delegations in practice. A background Devin session with full repo/test access would be needed to construct a concrete multi-stake numeric scenario (varying `num_tower_slots`, `total_slots`, `point_value.points`, and many small stake delegations) and run the existing reward-calculation unit tests / a custom test to determine whether the assert can actually trip.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L299-307)
```rust
            // In tower, `points` still needs to be scaled by `point_value` to calculate this
            // `vote_state` earned.
            // The final unwrap is safe, as points_value.points is guaranteed to be non zero above.
            tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
        }
```

**File:** runtime/src/inflation_rewards/mod.rs (L319-329)
```rust
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
```

**File:** runtime/src/inflation_rewards/mod.rs (L413-435)
```rust
fn commission_split_preserve_lamports(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
        split => {
            let staker_bps = MAX_BPS
                .checked_sub(split)
                .expect("commission cannot be greater than MAX_BPS");
            let staker_rewards = u128::from(on)
                .checked_mul(u128::from(staker_bps))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;
            let staker_rewards = staker_rewards as u64;
            let voter_rewards = on
                .checked_sub(staker_rewards)
                .expect("staker rewards cannot exceed total rewards");

            (voter_rewards, staker_rewards, true)
        }
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```
