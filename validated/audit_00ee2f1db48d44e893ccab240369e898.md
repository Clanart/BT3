Based on the evidence gathered, I can construct a solid Agave analog. The core broken invariant in the original report is: **a party who controls a mutable economic parameter (subscription "terms") can update it right up until the moment funds are distributed/settled, with no delay or commitment mechanism, extracting value from a counterparty who committed based on the old parameter.** Agave has protections against exactly this pattern for the legacy vote-account commission, but the newer basis-points commission path for block-revenue sharing appears to lack them.

### Title
Unrestricted, undelayed `BlockRevenue` commission updates allow commission-sniping that steals stake-delegator rewards - (File: `programs/vote/src/vote_state/mod.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The legacy vote-account commission (percentage-based) has two protections against a validator manipulating its cut of rewards right before distribution: a same-epoch timing restriction (`is_commission_update_allowed`) and a one-epoch delayed snapshot used at reward-calculation time (`delay_commission_updates`, SIMD-0249). The newer basis-points commission setter `update_commission_bps` (SIMD-0291/SIMD-0123), used for both `InflationRewards` and `BlockRevenue` commission kinds, explicitly has neither protection ("No commission update rule, per SIMD-0249 and SIMD-0291"). While `inflation_rewards_commission_bps` still benefits from the delayed-snapshot lookup in `redeem_delegation_rewards`, the `BlockRevenue` commission consumed by `calculate_block_reward` is read from `distribution_epoch_vote_accounts` (end-of-epoch/current state), not from the delayed snapshot.

### Finding Description
`update_commission_bps` allows the vote account's authorized withdrawer to change `block_revenue_commission_bps` at any slot, with no epoch-half restriction: [1](#0-0) 

Compare this with the legacy path, which enforces both a timing rule and (when `delay_commission_updates` is active) is disabled in favor of the delayed-snapshot design: [2](#0-1) [3](#0-2) 

The instruction handler wires this in without adding any additional guard for the `BlockRevenue` kind: [4](#0-3) 

At reward-calculation time, the inflation-rewards commission is deliberately read from a snapshot taken a full epoch earlier specifically "to prevent last minute commission rugs": [5](#0-4) [6](#0-5) 

However, the block-reward calculation is invoked with `cached_vote_accounts.distribution_epoch_vote_accounts` — the end-of-rewarded-epoch/current vote state, not the delayed snapshot used for inflation commission: [7](#0-6) 

This is structurally identical to the reported bug class: the "terms" (commission rate) that stakers implicitly rely on when delegating can be changed by the counterparty (vote account withdrawer) with no commitment, delay, or timing window, and the change takes effect at the exact moment rewards ("buy") are settled — letting the vote account operator capture a larger share of block-revenue rewards than delegators expected, at the delegators' expense.

### Impact Explanation
This causes fund loss for unprivileged SOL stakers: their share of block-revenue rewards can be silently reduced (and the validator's commission cut increased) at any point up to the exact slot rewards are computed/distributed, with zero warning window — precisely the "changing token terms right before settlement" primitive from the source report, applied to Agave's block-revenue-sharing commission (SIMD-0123/SIMD-0291) rather than to inflation rewards (which is protected). This is a fund-theft class impact within a builtin program (`programs/vote`) and the runtime's reward-distribution path (`runtime/src/bank/partitioned_epoch_rewards`).

### Likelihood Explanation
The actor required is only the vote account's `authorized_withdrawer` — an ordinary, unprivileged signer authority over their own vote account, not a malicious peer, leader, or trusted process. No consensus-level capability or leader slot is needed; the withdrawer simply submits an `UpdateCommissionBps(BlockRevenue, ...)` instruction at any slot, including immediately before the epoch boundary / reward-distribution point, since `update_commission_bps` has no timing gate at all.

### Recommendation
Apply the same protections used for `InflationRewards` commission to `BlockRevenue` commission: (1) reuse `is_commission_update_allowed`-style timing restriction, and/or (2) have `calculate_block_reward` read commission from the same one-epoch-delayed snapshot (`snapshot_epoch_vote_accounts`) rather than `distribution_epoch_vote_accounts`, so a commission change cannot affect rewards until a full epoch has elapsed.

### Proof of Concept
1. Validator operator delegates stake under vote account V with `block_revenue_commission_bps = 0`.
2. Stakers delegate to V expecting to receive (approximately) 100% of block-revenue rewards.
3. Near the end of the rewarded epoch (or any slot — no timing restriction applies), the withdrawer authority calls `UpdateCommissionBps { kind: BlockRevenue, commission_bps: 10000 }` via `update_commission_bps`, which succeeds unconditionally.
4. `calculate_stake_rewards_and_commissions` computes block rewards for the epoch using `distribution_epoch_vote_accounts` (current/end-of-epoch state), reflecting the newly raised 100% commission — even though delegators staked under the 0% commission for the entire epoch.
5. Delegators receive 0 block-revenue reward for the epoch; the validator captures all of it, with no delay window or notice, unlike the protected `InflationRewards` path.

I was not able to fully view the complete `calculate_block_reward` function body before running out of tool calls, so the exact arithmetic of how `block_revenue_commission_bps` is applied inside that function is not directly confirmed here — only that it is invoked with the undelayed `distribution_epoch_vote_accounts` rather than the delayed snapshot used for inflation commission. This should be verified by reading `calculate_block_reward`'s full implementation in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` before treating this as a confirmed exploit path. Due to index size limits, this snippet could not be fully retrieved; starting a Devin session with full repository access would allow tracing this end-to-end.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L797-825)
```rust
pub fn update_commission<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission: u8,
    signers: &HashSet<Pubkey, S>,
    epoch_schedule: &EpochSchedule,
    clock: &Clock,
    disable_commission_update_rule: bool,
) -> Result<(), InstructionError> {
    let vote_state_result = get_vote_state_handler_checked(vote_account, target_version);
    let enforce_commission_update_rule = !disable_commission_update_rule
        && match vote_state_result.as_ref() {
            Ok(decoded_vote_state) => commission > decoded_vote_state.commission(),
            Err(_) => true,
        };

    if enforce_commission_update_rule && !is_commission_update_allowed(clock.slot, epoch_schedule) {
        return Err(VoteError::CommissionUpdateTooLate.into());
    }

    let mut vote_state = vote_state_result?;

    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    vote_state.set_commission(commission);

    vote_state.set_vote_account_state(vote_account)
}
```

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

**File:** programs/vote/src/vote_processor.rs (L202-220)
```rust
        VoteInstruction::UpdateCommission(commission) => {
            let sysvar_cache = invoke_context.environment_config.sysvar_cache();

            // Disable the commission update rule after the "delay commission
            // update" feature is activated because it imposes a minimum delay
            // of one full epoch before the new commission rate takes effect.
            let disable_commission_update_rule =
                invoke_context.get_feature_set().delay_commission_updates;

            vote_state::update_commission(
                &mut me,
                target_version,
                commission,
                &signers,
                sysvar_cache.get_epoch_schedule()?.as_ref(),
                sysvar_cache.get_clock()?.as_ref(),
                disable_commission_update_rule,
            )
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L701-724)
```rust
        let vote_state = vote_account.vote_state_view();

        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L815-833)
```rust
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```
