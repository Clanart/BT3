## Title
`UpdateCommissionBps` (SIMD-0291) removes the epoch-delay guard that protects stakers from last-block commission changes - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The legacy `update_commission` path in the vote program enforces a timing guard so that a commission *increase* can only take effect if applied in the first half of an epoch, forcing at least one epoch of lead time before stakers are affected [1](#0-0) . The newer `UpdateCommissionBps` instruction (SIMD-0291), which sets `inflation_rewards_commission_bps` / `block_revenue_commission_bps` directly in basis points, explicitly drops this guard — the code comment states "No commission update rule, per SIMD-0249 and SIMD-0291" [2](#0-1) . This is architecturally identical to the reported Celo `updateCommission` bug: the withdraw authority of a vote account can raise the commission to 100% (10,000 bps) instantly, with the only remaining protection being a reward-calculation-time snapshot mechanism that some commission paths use and others may not.

### Finding Description
`update_commission_bps` is called from the vote processor whenever `commission_rate_in_basis_points` and `delay_commission_updates` features are active [3](#0-2) . Unlike `update_commission`, it performs no `is_commission_update_allowed`/`CommissionUpdateTooLate` check at all — the only requirement is a valid signature from the authorized withdrawer [4](#0-3) . This means the withdraw authority can set `inflation_rewards_commission_bps` or `block_revenue_commission_bps` to `10_000` (100%) in one slot, and back down again in a later slot, with the change visible in the live vote account state immediately.

Protection against this is only reintroduced downstream, at reward-calculation time, for the *inflation* commission path: `redeem_delegation_rewards` looks up a snapshot of the vote account taken a full epoch earlier (`snapshot_epoch_vote_accounts`) when `delay_commission_updates` is active, specifically to avoid "last minute commission rugs" [5](#0-4) [6](#0-5) . Crucially, this snapshot-delay logic is entirely separate from — and not enforced by — the vote-program instruction itself; it is a runtime-side mitigation that depends on the reward calculation code correctly wiring the historical snapshot for every place commission is consumed. The instruction-level invariant ("commission changes take effect only after a delay") that the fix in `update_commission`/`is_commission_update_allowed` was designed to guarantee is not actually enforced by `UpdateCommissionBps`; it is fully delegated to whichever downstream consumer happens to apply a snapshot.

I was not able to fully verify, within the available tool budget, whether the `block_revenue_commission_bps` field (used for SIMD-0123 block-revenue/fee sharing) is consumed through the same delayed/snapshotted vote-account view as inflation rewards, or whether some consumer (e.g., the per-block fee-distribution/deposit-to-delegator-pool logic) reads the *live* `block_revenue_commission_bps` value directly from the current vote account state. If any consumer of `block_revenue_commission_bps` (or any future consumer of `inflation_rewards_commission_bps` added without wiring the snapshot) reads the live value instead of a delayed snapshot, then the removal of the instruction-level delay in `update_commission_bps` reproduces the exact Celo bug: the withdraw authority calls `UpdateCommissionBps` immediately before a reward/fee distribution event, sets commission to 100%, captures the value, then can restore it afterward — with delegators/stakers unable to react in time because the change is not queued or timelocked at the point of mutation.

### Impact Explanation
If any commission consumer trusts the live vote-account commission value rather than a properly delayed snapshot, a malicious or compromised withdraw authority could redirect up to 100% of a given reward/fee distribution away from delegators to themselves for that event, then revert the commission — a direct fund-theft vector matching the original Celo H02 finding. Even where the snapshot mitigation is correctly wired (as it appears to be for `inflation_rewards_commission_bps`), the safety property is no longer guaranteed by the vote program's own instruction logic; it is an implicit assumption on every downstream caller, which is a fragile invariant to maintain across the codebase, especially for newer fields like `block_revenue_commission_bps` that were added later.

### Likelihood Explanation
Likelihood depends entirely on which commission fields lack a corresponding delayed-snapshot enforcement in reward/fee-distribution logic. This is an unprivileged action available to any vote account's own withdraw authority — no external validator/peer compromise is needed, only ordinary control over one's own vote account, which is within the described "unprivileged Agave issue" scope. Because I could not conclusively confirm the exact consumption path of `block_revenue_commission_bps` at fee-collection time within this session, likelihood should be treated as **plausible but unverified** for that specific field.

### Recommendation
- Confirm whether `block_revenue_commission_bps` (and any other commission-bps field) is always read from an epoch-delayed snapshot analogous to `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`, rather than from the live vote account state, at every point it is used to split rewards.
- If any consumer reads the live value, either (a) reintroduce an instruction-level timing guard in `update_commission_bps` similar to `is_commission_update_allowed`, or (b) guarantee that every consumer sources commission strictly from the epoch-delayed snapshot, with tests asserting this invariant explicitly (not just for inflation rewards).
- Add an explicit cross-module test that changes `block_revenue_commission_bps` mid-epoch via `UpdateCommissionBps` and asserts the change has no effect on already-in-flight fee/reward distribution for that epoch.

### Proof of Concept
Conceptual sequence (based on the code paths found; the exact downstream effect on `block_revenue_commission_bps` was not fully traced in this session):
1. Withdraw authority calls `UpdateCommissionBps { commission_bps: 10_000, kind: BlockRevenue }` — succeeds immediately with only a signature check, no epoch-timing check [2](#0-1) .
2. If the fee/reward distribution logic for that period reads the current vote-account state (rather than a delayed snapshot) for `block_revenue_commission_bps`, the validator captures 100% of the block-revenue-derived delegator pool for that distribution.
3. Withdraw authority calls `UpdateCommissionBps` again to restore the original rate, hiding the change from casual observers who only check the current commission value.

This mirrors the Celo `updateCommission` exploit: instruction-level acceptance of an unrestricted, instantaneous commission change, with correctness depending entirely on unverified downstream snapshotting.

### Citations

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
