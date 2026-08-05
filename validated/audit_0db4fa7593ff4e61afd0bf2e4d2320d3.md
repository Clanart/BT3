### Title
Commission-rug-pull protection is bypassed by the new bps-based `UpdateCommissionBps(InflationRewards)` instruction, which writes the same field as the legacy commission instruction without the epoch-half restriction - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The vote program enforces `commission_updates_only_allowed_in_first_half_of_epoch` on commission *increases* only through the legacy `update_commission()` path via `is_commission_update_allowed()`. The newer `update_commission_bps()` path (SIMD-0291/SIMD-0249), when called with `CommissionKind::InflationRewards`, writes to the exact same underlying state field (`inflation_rewards_commission_bps`) but has no timing restriction whatsoever, allowing a validator to increase its effective commission at any slot in the epoch, including immediately before the epoch-boundary reward calculation snapshot is taken.

### Finding Description
`VoteStateV4` stores a single field, `inflation_rewards_commission_bps: u16`, that backs both the legacy percent-based commission accessor and the new bps-based one: [1](#0-0) 

The legacy path, `update_commission()`, enforces `is_commission_update_allowed(clock.slot, epoch_schedule)` for any increase (`commission > decoded_vote_state.commission()`), rejecting late-epoch increases with `VoteError::CommissionUpdateTooLate`: [2](#0-1) [3](#0-2) 

The new path, `update_commission_bps()`, explicitly drops this rule for the `InflationRewards` kind, per the inline comment "No commission update rule, per SIMD-0249 and SIMD-0291": [4](#0-3) 

Both instructions mutate the same on-chain value — `update_commission()` calls `vote_state.set_commission(commission)`, which sets `inflation_rewards_commission_bps = commission * 100`, and `update_commission_bps(..., CommissionKind::InflationRewards, ...)` calls `set_inflation_rewards_commission_bps(commission_bps)`, which writes the identical field directly: [5](#0-4) 

This value is exactly what is read at reward-calculation time to determine the validator's cut of stake rewards (subject to the `commission_rate_in_basis_points` feature, which reads `inflation_rewards_commission_bps` directly instead of converting from the legacy percent field): [6](#0-5) 

The reward-calculation delay logic (`delay_commission_updates`) is a separate, independent mitigation that only defers *which epoch's* commission snapshot is used for reward math — it does not prevent the underlying state mutation, and its test suite (`test_calculate_stake_vote_rewards_new_vote_account`) demonstrates commission changes are still accepted and eventually honored regardless of which instruction wrote them: [7](#0-6) 

### Impact Explanation
The original "first-half-of-epoch" commission restriction exists specifically to prevent validators from raising their commission right before rewards are paid out ("last minute commission rugs" — explicitly called out in the code comment at `runtime/src/bank.rs:1717-1748` referencing this exact concern for cached vote-account snapshots). By routing the same state mutation through `update_commission_bps` with `CommissionKind::InflationRewards`, a validator operator can bypass this protection entirely and set an arbitrarily high commission at any point in the epoch — including the very last slot before the epoch boundary. Depending on activation of `delay_commission_updates`, this can still allow the operator to capture undisclosed/excess rewards from delegated stakers who chose the validator under a lower, previously observed commission, directly reducing staking rewards owed to those stakers. This mirrors the BakerFi pattern exactly: a protective guard (`whenNotPaused` / epoch-half check) exists on one entry point but a functionally equivalent entry point that mutates the same state bypasses it.

### Likelihood Explanation
This requires no malicious peer, admin, or trusted integration — any vote account's authorized withdrawer (the same signer authorized to call the legacy `update_commission`) can simply issue `UpdateCommissionBps` with `CommissionKind::InflationRewards` instead, which is a normal, unprivileged instruction available once `block_revenue_sharing`/SIMD-0291 support is live. No race condition or timing is needed beyond choosing to call the alternate instruction late in the epoch — making this trivially and reliably exploitable by any validator operator who wants to.

### Recommendation
Apply the same `is_commission_update_allowed()` check (guarded by the existing `disable_commission_update_rule`/`commission_updates_only_allowed_in_first_half_of_epoch` feature logic) to `update_commission_bps()` when `kind == CommissionKind::InflationRewards` and the new value represents an increase over the current `inflation_rewards_commission_bps`, so that both instructions enforce a consistent anti-rug-pull policy on the same underlying field.

### Proof of Concept
1. Validator operator holds `authorized_withdrawer` for a vote account with current `inflation_rewards_commission_bps = 1000` (10%).
2. Near the end of an epoch (past the epoch-half boundary), the operator calls `update_commission(..., commission=50, ...)` (legacy percent path) — this fails with `VoteError::CommissionUpdateTooLate` per `is_commission_update_allowed`.
3. The same operator instead calls the new instruction path invoking `update_commission_bps(&mut vote_account, target_version, 5000, CommissionKind::InflationRewards, &signers, block_revenue_sharing_enabled)`.
4. Per [4](#0-3) , this call succeeds unconditionally (only checks signer + block-revenue gate for `BlockRevenue` kind, not `InflationRewards`), setting `inflation_rewards_commission_bps = 5000` (50%) despite being in the restricted second half of the epoch.
5. At epoch-boundary reward calculation, this new 50% commission (or the delayed snapshot value, depending on `delay_commission_updates`) is used to compute the validator's share of stake rewards via `redeem_rewards`, extracting a much larger cut from delegators than the epoch-half protection was designed to allow.

**Uncertainty note:** I could not fully verify from the indexed code whether `delay_commission_updates` is *always* active in current mainnet-equivalent feature configuration (which would delay when this manipulated value is actually used for reward math by one epoch) or whether it can be independently deactivated/is still gated as a feature — this affects the exact exploitation window (immediate vs. one-epoch-delayed rug) but does not change the fact that the state mutation itself bypasses the intended timing guard.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L134-158)
```rust
    pub(crate) fn commission(&self) -> u8 {
        match &self.target_state {
            TargetVoteState::V4(v4) => {
                (v4.inflation_rewards_commission_bps / 100).min(u8::MAX as u16) as u8
            }
        }
    }

    #[allow(clippy::arithmetic_side_effects)]
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn set_commission(&mut self, commission: u8) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                // Safety: u16::MAX > u8::MAX * 100
                v4.inflation_rewards_commission_bps = (commission as u16) * 100;
            }
        }
    }

    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn set_inflation_rewards_commission_bps(&mut self, commission_bps: u16) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.inflation_rewards_commission_bps = commission_bps,
        }
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L796-825)
```rust
/// Update the vote account's commission
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1976-2052)
```rust
    #[test_case(true; "delay_commission_updates")]
    #[test_case(false; "instant_commission_updates")]
    fn test_calculate_stake_vote_rewards_new_vote_account(delay_commission_updates: bool) {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        if !delay_commission_updates {
            deactivate_features(&mut genesis_config, &vec![delay_commission_updates::id()]);
        }

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();
        let vote_address = Pubkey::new_unique();

        // No reward should be given in the epoch that a vote account is
        // delegated to for the first time
        let mut bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        create_with_balance: Some(LAMPORTS_PER_SOL),
                        new_commission: Some(1),
                        earned_credits: Some(1000),
                        delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                        ..VoteOperations::default()
                    },
                )],
            },
        );

        // Check that if a vote account didn't exist two epochs ago (normal for
        // new vote accounts), that the reward commission falls back to the
        // commission from the end of the rewarded epoch.
        bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 1,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        new_commission: Some(2),
                        earned_credits: Some(1000),
                        expect_reward: true,
                        ..VoteOperations::default()
                    },
                )],
            },
        );

        apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 2,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        new_commission: Some(3),
                        earned_credits: Some(1000),
                        expect_reward: true,
                        ..VoteOperations::default()
                    },
                )],
            },
        );
    }
```
