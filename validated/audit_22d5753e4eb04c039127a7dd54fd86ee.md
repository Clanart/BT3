Based on the investigation, I found a strong Agave analog in the vote-account commission / stake-rewards distribution logic. Note: I was not able to open the full body of `calculate_block_reward` (only its call site) due to running out of tool iterations, so the exact internal handling of `block_revenue_commission_bps` inside that function is not fully confirmed — this is called out explicitly below.

### Title
Block-revenue commission is not subject to the "delay commission update" anti-rug protection that inflation-rewards commission receives - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The external report describes an "artist" (a privileged-but-bounded actor) who can spike a fee parameter immediately before a value-distribution event and revert it afterward, stealing value from unprivileged counterparties (buyers/affiliates) with no on-chain delay to prevent it. In Agave, the vote program's `authorized_withdrawer` is the analogous privileged-but-bounded actor over commission, and Agave has an explicit, purpose-built defense against exactly this pattern for inflation-rewards commission — but that defense is not applied to block-revenue-sharing commission in the reward calculation path.

### Finding Description
For inflation rewards, `redeem_delegation_rewards` deliberately reads the commission rate from a **delayed snapshot** (`snapshot_epoch_vote_accounts` / `rewarded_epoch_vote_accounts`, i.e., state from a full epoch prior) when `delay_commission_updates` is active, specifically to prevent "last minute commission rugs": [1](#0-0) 

This delayed-snapshot mechanism is documented at the `CachedVoteAccounts` struct level as existing "to prevent last minute commission rugs": [2](#0-1) 

However, `calculate_stake_rewards_and_commissions` computes the block-revenue-sharing reward via `calculate_block_reward`, passing `cached_vote_accounts.distribution_epoch_vote_accounts` directly — this is the **current, undelayed** vote-account state (end of rewarded epoch / start of distribution epoch), not the epoch-prior snapshot used for inflation commission: [3](#0-2) 

Separately, the new `UpdateCommissionBps` instruction (SIMD-0291), which governs both `InflationRewards` and `BlockRevenue` commission kinds, explicitly has **no timing restriction** — unlike the legacy `UpdateCommission` instruction, which is gated by `is_commission_update_allowed`/`CommissionUpdateTooLate` to only permit increases in the first half of an epoch: [4](#0-3) [5](#0-4) 

The corrupted value is `block_revenue_commission_bps` in `VoteStateV4`: it can be maxed out by the withdraw authority immediately before the block-revenue reward for the epoch is computed and paid, then reduced back afterward — with no equivalent of the one-epoch delay that protects `inflation_rewards_commission_bps`.

### Impact Explanation
If block-revenue commission is read live (undelayed) at reward-calculation time, a validator's authorized withdrawer can unilaterally and atomically raise `block_revenue_commission_bps` to the maximum right before the block-revenue payout for an epoch/slot is computed, extracting a larger cut of delegators' block-revenue rewards than delegators could have anticipated, then lower it back immediately after — a direct fund-theft-from-unprivileged-stakers pattern, mirroring the "artist front-running buy() to steal fees" bug class. This causes real fund loss/misallocation for delegators who have no way to react (no mempool visibility or slippage protection exists for reward distribution, unlike a DEX trade).

### Likelihood Explanation
Uncertain/Medium: I confirmed that (a) the delayed-snapshot protection exists and is explicitly motivated by anti-rug concerns for inflation commission, (b) `UpdateCommissionBps` has no timing gate, and (c) the block-revenue reward calculation call site passes the undelayed `distribution_epoch_vote_accounts`. I was **not able to inspect the full body of `calculate_block_reward`** to verify whether it internally re-derives commission from a delayed source by some other mechanism (e.g., via `stake_history` or a separate historical lookup) before running out of tool budget. This is the key open question that determines whether this is an actual live bug or an already-mitigated case.

### Recommendation
Verify inside `calculate_block_reward` (in the block-revenue reward path) whether `block_revenue_commission_bps` is read from `distribution_epoch_vote_accounts` (current) or from a delayed snapshot equivalent to the inflation-rewards path. If it reads the current/live value, apply the same one-epoch-delay protection used for `inflation_rewards_commission_bps` (via `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`) to `block_revenue_commission_bps`, and/or extend the `is_commission_update_allowed` timing gate to `UpdateCommissionBps` for the `BlockRevenue` kind.

### Proof of Concept
1. Validator's authorized withdrawer submits `VoteInstruction::UpdateCommissionBps { commission_bps: MAX, kind: CommissionKind::BlockRevenue }` — allowed at any point in the epoch since no timing restriction applies (`programs/vote/src/vote_processor.rs:362-382`).
2. If `calculate_block_reward` reads `block_revenue_commission_bps` from `distribution_epoch_vote_accounts` (the same undelayed data structure it is passed at `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:820-833`), the inflated commission is applied to that epoch's block-revenue payout immediately.
3. Validator submits a second `UpdateCommissionBps` instruction lowering the commission back down after the reward is computed/paid, restoring the appearance of a normal, low-commission validator to stakers monitoring on-chain state, having captured excess commission for that single distribution with no epoch-delay defense in effect (unlike the inflation-rewards path).

This PoC's step 2 depends on the unconfirmed internals of `calculate_block_reward`; a Devin session with full repo access should trace that function body to confirm or refute the live-read behavior before treating this as fully validated.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
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

**File:** programs/vote/src/vote_state/mod.rs (L797-826)
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
