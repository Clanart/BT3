### Title
`UpdateCommissionBps` (block-revenue commission) has no update-timing rule, enabling a "commission rug" against delegators - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The report describes a class of bug where a user is allowed to freely mutate a parameter that will later be consumed at a "finalization" step, with no restriction tying the mutation to a stable point in time, allowing the user to game the outcome. The Agave analog is the vote program's new `UpdateCommissionBps` / `update_commission_bps` instruction (SIMD-0291, "Commission Rate in Basis Points"): unlike the legacy `UpdateCommission` path, which enforces a documented anti-rug timing rule (`is_commission_update_allowed`) and is further protected at reward-calculation time by a full-epoch delay (`delay_commission_updates`), the new basis-points commission has **no update-timing rule at all**, and the block-revenue-sharing commission specifically is read from the *live, undelayed* vote-account snapshot at reward-calculation time.

### Finding Description
The legacy commission update path enforces two independent defenses against "last-minute commission rugs":
1. `update_commission` blocks commission increases in the second half of an epoch via `is_commission_update_allowed`: [1](#0-0) 
2. Even when a commission change is accepted, the reward-calculation code deliberately looks back a full epoch (`snapshot_epoch_vote_accounts`) rather than using the live commission, explicitly to prevent "last minute commission rugs": [2](#0-1) 

The new `UpdateCommissionBps` instruction, added for SIMD-0291 (basis-point commission, including the new `BlockRevenue` commission kind for SIMD-0123 block-revenue sharing), explicitly removes the timing rule: [3](#0-2) [4](#0-3) 

The comment in `update_commission_bps` states plainly: "No commission update rule, per SIMD-0249 and SIMD-0291," meaning the authorized withdrawer of a vote account can set `inflation_rewards_commission_bps` or `block_revenue_commission_bps` at any slot, with no gating like `is_commission_update_allowed`.

At reward-distribution time, the *inflation* commission is still protected by the epoch-delayed snapshot lookup when `delay_commission_updates` is active: [5](#0-4) 

However, the *block-revenue* commission used by `calculate_block_reward` is computed directly from `cached_vote_accounts.distribution_epoch_vote_accounts` — the live, end-of-epoch/beginning-of-distribution-epoch vote state — not the epoch-delayed `snapshot_epoch_vote_accounts`: [6](#0-5) 

So the corrupted value is the `block_revenue_commission_bps` field consumed by `calculate_block_reward`: it can be freely toggled by the vote account's own authorized withdrawer up to the moment the epoch-boundary reward snapshot is taken, with none of the guards ("commission update rule" and "one full epoch delay") that were specifically built to stop this exact class of abuse for the legacy/inflation commission fields.

### Impact Explanation
A vote-account owner/authorized withdrawer (an ordinary, unprivileged staking-pool-style participant, not requiring control over consensus, gossip, or another validator's identity) can set `block_revenue_commission_bps` low while soliciting/holding delegated stake, then spike it to the maximum right before the epoch boundary at which block-revenue rewards are computed and distributed, extracting a disproportionate share of block revenue from delegators who had no on-chain guarantee of a stable commission rate. This is a direct fund-loss vector for stake delegators, mirroring the original report's "block until favorable, then unblock to capture the favorable outcome" pattern — except here the "favorable outcome" is the delegator's share of block revenue rather than trade price.

### Likelihood Explanation
Likelihood is high: exploiting this requires no special privilege beyond being the authorized withdrawer of one's own vote account (a role any staking-pool operator legitimately holds), and the instruction imposes no restriction on timing or frequency of updates, as explicitly documented in the code comment ("No commission update rule, per SIMD-0249 and SIMD-0291").

### Recommendation
Apply the same one-epoch-delay lookup used for `inflation_rewards_commission` to `block_revenue_commission_bps` in `redeem_delegation_rewards`/`calculate_block_reward` — i.e., source the block-revenue commission from `snapshot_epoch_vote_accounts` (or `rewarded_epoch_vote_accounts`) rather than `distribution_epoch_vote_accounts` when `delay_commission_updates` is active — so both commission types are subject to the same anti-rug protection.

### Proof of Concept
Not independently executable from static review alone; the exploit path is: (1) authorized withdrawer calls `UpdateCommissionBps { kind: BlockRevenue, commission_bps: low }` early in an epoch, (2) attracts/retains delegated stake, (3) calls `UpdateCommissionBps { kind: BlockRevenue, commission_bps: max }` right before the epoch boundary, (4) `calculate_block_reward` reads the just-updated `distribution_epoch_vote_accounts` commission at reward calculation, paying the attacker the higher commission on the full epoch's block revenue with no delay protection, as traced through [4](#0-3)  and [6](#0-5) . I was not able to build/run a full bank-rewards integration test to confirm the exact lamport delta in this session; a Devin agent with repo access should extend the existing `test_calculate_stake_vote_rewards_*` tests in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` with a `block_revenue_sharing` + `delay_commission_updates` scenario that updates `block_revenue_commission_bps` mid/late-epoch to confirm the live (undelayed) value is used.

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
