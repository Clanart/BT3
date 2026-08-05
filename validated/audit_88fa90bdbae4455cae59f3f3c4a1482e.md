## Analysis

The Solidity bug is a "retroactive-rate" class issue: a parameter that scales *already-accrued* value (`Boost`'s multiplier) is changed by the user before the pending value is checkpointed/paid out at the old rate, letting them collect boosted rewards for a period during which they weren't actually boosted.

Agave has the exact analog in the stake-reward/vote-commission machinery, and Agave's own code shows the maintainers are aware of the bug class and built a guard for it — but the guard has a gap for newly-created vote accounts.

### Title
Vote-account commission changes are not delay-protected for newly created vote accounts, letting a validator retroactively raise commission on an already-earned epoch of stake rewards - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`redeem_delegation_rewards` computes the commission split applied to an entire epoch's accrued stake-reward points using a commission rate that is supposed to be delayed by a full epoch (feature `delay_commission_updates`, SIMD-0249) specifically "to prevent last minute commission rugs" [1](#0-0) . However, the lookup falls back to the *current* (undelayed) vote-state commission whenever the vote account is not present in the one/two-epoch-old snapshots — which is always true for a vote account created within the last couple of epochs [2](#0-1) . Combined with the fact that basis-point commission updates (SIMD-0291/`UpdateCommissionBps`) have "no timing restrictions" at all [3](#0-2) [4](#0-3) , a validator can advertise a low/zero commission while accruing an entire epoch of delegator credits, then raise commission to (near) 100% right before that epoch's rewards are calculated, and the new rate is applied to the whole epoch's already-earned points because the new-account fallback bypasses the delay entirely.

### Finding Description
`get_cached_vote_accounts` builds two historical snapshots specifically to delay the effect of commission changes by a full epoch: [5](#0-4) 

`redeem_delegation_rewards` uses these snapshots to pick the commission rate for splitting the *entire rewarded epoch's* accrued points: [2](#0-1) 

The critical corrupted value is `commission_bps` (and `vote_state_for_commission`): when `snapshot_epoch_vote_accounts` and `rewarded_epoch_vote_accounts` both lack an entry for the vote account (true for any vote account younger than ~2 epochs), the code falls back to `vote_state`, i.e. the **current, distribution-time** vote account state — completely defeating the delay. This exact fallback behavior is asserted as intended in the test suite: [6](#0-5) [7](#0-6) 

Meanwhile, the newer basis-points commission-update path (`UpdateCommissionBps`, gated by `commission_rate_in_basis_points` + `delay_commission_updates`) removes even the legacy epoch-half timing check (`is_commission_update_allowed`) that the old `update_commission` enforced: [8](#0-7) [9](#0-8) 

So the *only* protection against a mid/end-of-epoch commission hike retroactively taxing an entire epoch's stake rewards is the epoch-snapshot delay in `redeem_delegation_rewards` — and that delay is exactly what is skipped for vote accounts that are new relative to the rewarded epoch. This is structurally identical to `Boost.setLockStatus()` applying a newly-set boost factor to rewards that accrued before the change, because `earned()`/reward-split logic uses a single "current" rate over the whole unclaimed/unresolved accrual window instead of checkpointing at the old rate first.

### Impact Explanation
A vote account's authorized withdrawer can capture almost all of a full epoch's worth of delegators' earned inflation stake rewards by raising `inflation_rewards_commission_bps` (or legacy `commission`, subject to the weaker basis-point path or by acting within the same-epoch window before distribution) right before that epoch's rewards are calculated, as long as the vote account is within its first ~2 epochs of existence. This is direct fund theft from unprivileged stakers/delegators who delegated in good faith at the advertised low commission — the exact "false accounting/fund loss" impact class this task targets.

### Likelihood Explanation
Creating a new vote account, advertising near-zero commission to attract delegations for one epoch, and then raising commission via `UpdateCommissionBps` (which has no timing restriction per SIMD-0249/0291) requires no special privilege beyond being the vote account's authorized withdrawer — an action any validator operator can take unilaterally against their own delegators. The precondition (vote account "not found in epoch-old snapshot") is the default state for every newly created vote account and is exercised routinely (confirmed by the existing test cases documenting this exact fallback behavior).

### Recommendation
Close the fallback gap: when a vote account is absent from both `snapshot_epoch_vote_accounts` and `rewarded_epoch_vote_accounts`, either skip/zero the reward for that epoch (as is already done for stake activation epoch) instead of falling back to `distribution_epoch_vote_accounts`'s current state, or otherwise ensure the commission used for a rewarded epoch's payout is always the commission as it stood at (or before) the start of that rewarded epoch, with no "new account" exception. Additionally, consider re-adding a timing restriction analogous to `is_commission_update_allowed` for `UpdateCommissionBps`, so a commission increase cannot take effect within the same epoch it is meant to be delayed into.

### Proof of Concept
1. Validator creates vote account `V` with `inflation_rewards_commission_bps = 0` during epoch `E`, and delegators stake to `V` in epoch `E` (or earlier).
2. `V` earns vote credits through epoch `E`. Because `V` did not exist at `epoch_stakes(E)` (or does exist in `epoch_stakes` but is still within the lookback window described in `test_calculate_stake_vote_rewards_new_vote_account`/`test_calculate_stake_vote_rewards_prestaked_vote_account`), neither `snapshot_epoch_vote_accounts` nor `rewarded_epoch_vote_accounts` contains a usable prior commission for `V` when rewards for epoch `E` are computed.
3. At the very start of epoch `E+1` (or any point before/at the reward calculation for `rewarded_epoch = E`), the validator issues `VoteInstruction::UpdateCommissionBps { commission_bps: 10_000, .. }` — allowed instantly, no epoch-half check.
4. `calculate_stake_rewards_and_commissions` → `redeem_delegation_rewards` computes `commission_bps` via the `delay_commission_updates` branch, falls through both `.and_then`/`.or_else` lookups (no snapshot entry for `V`), and uses `vote_state` (current, just-raised to 10000 bps) to split the delegators' entire epoch-`E` accrued reward points.
5. Delegators who staked expecting 0% commission for epoch `E` instead receive ~0 rewards for that epoch; the validator's commission collector receives the full inflation reward for the epoch. [10](#0-9)

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2016-2034)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2085-2104)
```rust
        // Check that if a new vote account is somehow already staked and
        // earning rewards in the epoch in which it was created, the reward
        // commission falls back to the latest commission rate for that epoch
        bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 1,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        create_with_balance: Some(LAMPORTS_PER_SOL),
                        new_commission: Some(1),
                        earned_credits: Some(1000),
                        expect_reward: true,
                        ..VoteOperations::default()
                    },
                )],
            },
        );
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

**File:** programs/vote/src/vote_state/mod.rs (L1806-1811)
```rust
    /// Test update_commission_bps (SIMD-0291).
    ///
    /// Unlike test_update_commission, SIMD-0291 has no timing restrictions
    /// (per SIMD-0249). Updates are always allowed regardless of epoch position.
    ///
    /// This test only uses V4 since SIMD-0291 depends on SIMD-0185 (VoteStateV4).
```
