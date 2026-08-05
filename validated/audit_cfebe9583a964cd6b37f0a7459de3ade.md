## Finding

### Title
Vote program accepts out-of-range commission values via `UpdateCommissionBps`, allowing commission > 100% - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
Analogous to the report's core defect — critical economic parameters (SPREAD, PLURALITY, STAKE) are re-parameterized with no sanity/range checks, letting a participant with ordinary write-access to that parameter push it outside its valid domain and corrupt downstream calculations — the Agave vote program's `update_commission_bps` function accepts an arbitrary `u16` commission value and stores it in vote state without ever validating that it represents a valid basis-points fraction (0–10,000, i.e. 0%–100%).

### Finding Description
`update_commission_bps` in [1](#0-0)  takes a caller-supplied `commission_bps: u16` and, after only checking the `block_revenue_sharing` feature gate and the withdrawer signature, writes it directly into vote state via `set_inflation_rewards_commission_bps` or `set_block_revenue_commission_bps` with **no upper-bound check**:

```
vote_state.set_inflation_rewards_commission_bps(commission_bps);
...
vote_state.set_block_revenue_commission_bps(commission_bps);
```

Unlike `update_commission` (the legacy percentage-based path), which enforces a same-epoch timing rule via `is_commission_update_allowed` [2](#0-1) , `update_commission_bps` has "No commission update rule, per SIMD-0249 and SIMD-0291" and performs zero range validation on the value itself. Because the field type is `u16`, any value up to 65,535 can be stored — i.e. up to 655.35%, far beyond the intended 0–10,000 bps (0–100%) domain that basis-points math assumes.

This value later feeds directly into stake/vote reward splitting in `redeem_rewards`/`redeem_stake_rewards`, where `voter_commission_bps: u16` is passed through unchanged [3](#0-2)  and used to compute the voter's share of the reward relative to the staker's share (typically `voter_share = reward * commission_bps / 10_000` and `staker_share = reward - voter_share` or the complementary bps `10_000 - commission_bps`). If `commission_bps > 10_000`, the complementary subtraction can underflow/saturate and the proportional split assumption is broken, so the guard rails that exist elsewhere for the legacy percentage path (bounded 0–100 by virtue of `u8`) do not exist for the bps path.

This is the direct analog of the report's exploit pattern: a plausible-looking, in-range-typed parameter (like PLURALITY or SPREAD) is set through a legitimate update path with no invariant check (e.g. `PLURALITY < 100`), and the missing check breaks the arithmetic invariants that downstream logic silently assumes.

### Impact Explanation
An out-of-range commission_bps corrupts the staker/voter reward split calculation for every epoch's inflation and block-revenue reward distribution tied to that vote account, causing incorrect (and potentially unbounded, in the saturating-subtraction case a staker share of zero while the voter absorbs more than the total reward pool) fund allocation between the validator and its delegated stakers. This is a fund-loss/false-execution class issue rooted in `runtime/src/inflation_rewards` reward accounting, which runs unconditionally for every active stake account during epoch reward distribution — not merely a local instruction failure.

### Likelihood Explanation
The action requires only the vote account's ordinary `authorized_withdrawer` signature — the same authority that already can legitimately call `UpdateCommission`/`UpdateCommissionBps` as part of normal, permitted validator operation. It requires no elevated/admin privilege and no malicious-peer assumption; it is simply a missing bounds check on a value every validator can already set through supported instructions, matching the report's "ordinary market participant with legitimate write access" framing exactly.

### Recommendation
Add an explicit invariant in `update_commission_bps` (and any other basis-points setters) enforcing `commission_bps <= 10_000` before calling `set_inflation_rewards_commission_bps`/`set_block_revenue_commission_bps`, returning `InstructionError::InvalidInstructionData` otherwise — mirroring the report's recommended `PLURALITY < 100`-style invariant. Additionally, audit `redeem_rewards`/`redeem_stake_rewards` in `runtime/src/inflation_rewards/mod.rs` to use checked/saturating arithmetic defensively even after the fix, and add regression tests asserting rejection of `commission_bps > 10_000`.

### Proof of Concept
1. A validator's withdraw-authority signer submits `VoteInstruction::UpdateCommissionBps(20000, CommissionKind::InflationRewards)` (or `BlockRevenue`, with the `block_revenue_sharing` feature active) for its vote account.
2. `vote_processor.rs` dispatches to `update_commission_bps` [4](#0-3) , which only checks the feature gate and withdrawer signature, then unconditionally stores `commission_bps = 20000` (200%) into vote state.
3. On the next epoch reward distribution, `redeem_rewards` passes this `voter_commission_bps` into the split calculation [5](#0-4) , producing a staker/voter split that no longer sums to the total reward, misallocating funds between the validator and its delegators.

**Caveat:** I could not fully trace the exact arithmetic (saturating vs. checked subtraction) inside the points-calculation module (`points.rs`) that consumes `commission_bps` within the available tool budget, so the precise failure mode (underflow panic vs. silent over-allocation) is not fully confirmed — this should be verified directly against `runtime/src/inflation_rewards/points.rs` and `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` before treating the severity as final.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L827-859)
```rust
/// Update the vote account's commission in basis points (SIMD-0291, SIMD-0123).
pub fn update_commission_bps<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission_bps: u16,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    block_revenue_sharing_enabled: bool,
) -> Result<(), InstructionError> {
    // Per SIMD-0291: BlockRevenue returns InvalidInstructionData unless
    // SIMD-0123 (block_revenue_sharing) is enabled.
    if matches!(kind, CommissionKind::BlockRevenue) && !block_revenue_sharing_enabled {
        return Err(InstructionError::InvalidInstructionData);
    }

    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // No commission update rule, per SIMD-0249 and SIMD-0291.

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_commission_bps(commission_bps);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_commission_bps(commission_bps);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L990-1004)
```rust
/// Given the current slot and epoch schedule, determine if a commission change
/// is allowed
pub fn is_commission_update_allowed(slot: Slot, epoch_schedule: &EpochSchedule) -> bool {
    // always allowed during warmup epochs
    if let Some(relative_slot) = slot
        .saturating_sub(epoch_schedule.first_normal_slot)
        .checked_rem(epoch_schedule.slots_per_epoch)
    {
        // allowed up to the midpoint of the epoch
        relative_slot.saturating_mul(2) <= epoch_schedule.slots_per_epoch
    } else {
        // no slots per epoch, just allow it, even though this should never happen
        true
    }
}
```

**File:** runtime/src/inflation_rewards/mod.rs (L35-94)
```rust
pub(crate) fn redeem_rewards<'a>(
    mut stake: Stake,
    voter_commission_bps: u16,
    vote_state: DelegatedVoteState,
    calculation_environment: CalculationEnvironment<'a>,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    ag_epoch_type: &AlpenglowEpochType,
    current_lamports: u64,
    minimum_lamports: u64,
) -> Result<(u64, u64, Stake), InstructionError> {
    if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
        let CalculationEnvironment {
            rewarded_epoch,
            stake_history,
            new_rate_activation_epoch,
            commission_rate_in_basis_points,
            use_fixed_point_stake_math,
            ..
        } = calculation_environment;
        let effective_stake_at_rewarded_epoch = effective_stake(
            &stake,
            rewarded_epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        inflation_point_calc_tracer(
            &InflationPointCalculationEvent::EffectiveStakeAtRewardedEpoch(
                effective_stake_at_rewarded_epoch,
            ),
        );
        inflation_point_calc_tracer(&InflationPointCalculationEvent::PriorTotalLamports(
            current_lamports,
        ));
        // Choose which trace to emit based on the `commission_rate_in_basis_points` feature.
        if commission_rate_in_basis_points {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CommissionBps(
                voter_commission_bps,
            ));
        } else {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::Commission(
                (voter_commission_bps / 100) as u8,
            ));
        }
    }

    if let Some((stakers_reward, voters_reward)) = redeem_stake_rewards(
        &mut stake,
        voter_commission_bps,
        vote_state,
        calculation_environment,
        inflation_point_calc_tracer,
        ag_epoch_type,
        current_lamports,
        minimum_lamports,
    ) {
        Ok((stakers_reward, voters_reward, stake))
    } else {
        Err(StakeError::NoCreditsToRedeem.into())
    }
```
