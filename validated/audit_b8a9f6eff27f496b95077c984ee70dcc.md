Audit Report

## Title
`UpdateCommissionBps` (block-revenue commission) has no update-timing rule, enabling a "commission rug" against delegators - ([File: programs/vote/src/vote_state/mod.rs])

## Summary
The vote program's `update_commission_bps` instruction (added for SIMD-0291/SIMD-0123) allows the authorized withdrawer of a vote account to change `inflation_rewards_commission_bps` and `block_revenue_commission_bps` at any slot, with no analog to the legacy `is_commission_update_allowed` timing gate. While the reward-calculation path for inflation commission still applies a one-full-epoch-delayed lookup via `snapshot_epoch_vote_accounts` when `delay_commission_updates` is active, the block-revenue commission consumed by `calculate_block_reward` is sourced from `cached_vote_accounts.distribution_epoch_vote_accounts`, the live/undelayed vote state, leaving block-revenue commission unprotected against last-minute manipulation.

## Finding Description
`update_commission_bps` in `programs/vote/src/vote_state/mod.rs` explicitly states "No commission update rule, per SIMD-0249 and SIMD-0291" and applies neither `is_commission_update_allowed` nor any other slot-based restriction before writing `inflation_rewards_commission_bps` or `block_revenue_commission_bps` [1](#0-0) . This contrasts with the legacy `update_commission`, which blocks commission increases in the back half of an epoch via `is_commission_update_allowed` [2](#0-1) .

At reward time, `get_cached_vote_accounts` in `runtime/src/bank.rs` produces three views of vote-account state — `snapshot_epoch_vote_accounts` (delayed by a full epoch specifically "to prevent last minute commission rugs"), `rewarded_epoch_vote_accounts`, and `distribution_epoch_vote_accounts` (live) [3](#0-2) . The inflation-commission computation in `calculation.rs` correctly consults the delayed snapshot when `delay_commission_updates` is enabled [4](#0-3) . However, `calculate_block_reward` is invoked with `cached_vote_accounts.distribution_epoch_vote_accounts` directly, bypassing the delayed-snapshot mechanism entirely [5](#0-4) . The vote_processor dispatch confirms `UpdateCommissionBps` is feature-gated only on `commission_rate_in_basis_points`/`delay_commission_updates` being enabled, not on any timing restriction [6](#0-5) .

This means the two purpose-built anti-rug defenses that exist for the legacy/inflation commission path (the update-timing rule and the epoch-delayed snapshot lookup) are both absent for the new block-revenue commission field, despite the code comments in `get_cached_vote_accounts` and `update_commission` making clear that timing protection against commission manipulation is a recognized design requirement.

## Impact Explanation
This is a fund-loss vector for stake delegators in the runtime rewards path: an authorized withdrawer of a vote account (a role any staking-pool operator or vote-account creator legitimately and permissionlessly holds, requiring no consensus/validator privilege) can set `block_revenue_commission_bps` low to attract stake and spike it to the maximum immediately before the epoch-boundary reward snapshot, capturing an outsized share of block revenue at delegators' expense with no on-chain guard against it. The corrupted value is the `block_revenue_commission_bps` field read by `calculate_block_reward` from the live `distribution_epoch_vote_accounts` state rather than a delayed snapshot.

## Likelihood Explanation
High likelihood: exploitation requires only ordinary control of one's own vote account (authorized withdrawer signature), a role obtainable by any permissionless actor, and no restriction — timing-based or otherwise — currently prevents repeated, last-minute commission changes on the block-revenue path, as explicitly documented in the `update_commission_bps` code comment itself.

## Recommendation
Route `block_revenue_commission_bps` through the same one-epoch-delayed lookup mechanism (`snapshot_epoch_vote_accounts` / `rewarded_epoch_vote_accounts`) already used for `inflation_rewards_commission` when `delay_commission_updates` is active, rather than sourcing it from the live `distribution_epoch_vote_accounts` in `calculate_block_reward`. Alternatively, reinstate a timing-based update rule (analogous to `is_commission_update_allowed`) specifically for commission increases on the block-revenue commission kind.

## Proof of Concept
1. Enable `block_revenue_sharing`, `commission_rate_in_basis_points`, and `delay_commission_updates` features.
2. As authorized withdrawer, call `UpdateCommissionBps { kind: BlockRevenue, commission_bps: low }` early in an epoch to attract stake.
3. Immediately before the epoch-boundary reward-calculation snapshot, call `UpdateCommissionBps { kind: BlockRevenue, commission_bps: max }`.
4. Observe that `calculate_block_reward`, invoked with `cached_vote_accounts.distribution_epoch_vote_accounts` [5](#0-4) , uses the just-updated high commission for the entire epoch's block-revenue distribution, unlike the inflation-commission path which would have used the delayed snapshot value [4](#0-3) .
5. Extend `test_calculate_stake_vote_rewards_*` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` with a block-revenue-sharing scenario that updates `block_revenue_commission_bps` late in the epoch to confirm the live (undelayed) value is what gets paid out.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L797-815)
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

**File:** runtime/src/bank.rs (L1723-1748)
```rust
    /// Get cached vote account state from the past few epochs so that some vote
    /// state configuration changes are delayed before being used in reward
    /// calculation.
    fn get_cached_vote_accounts<'a>(
        &'a self,
        rewarded_epoch: Epoch,
        distribution_epoch_vote_accounts: &'a VoteAccounts,
    ) -> CachedVoteAccounts<'a> {
        // Snapshot of vote account state from the beginning of the epoch prior to
        // the rewarded epoch. This snapshot state is saved a full epoch before
        // being used to prevent last minute commission rugs.
        let snapshot_epoch_vote_accounts = self
            .epoch_stakes(rewarded_epoch)
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());

        // Vote account state from the beginning of the rewarded epoch.
        let rewarded_epoch_vote_accounts = self
            .epoch_stakes(self.epoch())
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());

        CachedVoteAccounts {
            snapshot_epoch_vote_accounts,
            rewarded_epoch_vote_accounts,
            distribution_epoch_vote_accounts,
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L703-724)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L815-849)
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

**File:** programs/vote/src/vote_processor.rs (L362-382)
```rust
        VoteInstruction::UpdateCommissionBps {
            commission_bps,
            kind,
        } => {
            // SIMD-0291: Commission Rate in Basis Points
            // Requires SIMD-0185: Vote State V4
            // Requires SIMD-0249: Delay Commission Updates
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points || !feature_set.delay_commission_updates
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            vote_state::update_commission_bps(
                &mut me,
                target_version,
                commission_bps,
                kind,
                &signers,
                feature_set.block_revenue_sharing,
            )
        }
```
