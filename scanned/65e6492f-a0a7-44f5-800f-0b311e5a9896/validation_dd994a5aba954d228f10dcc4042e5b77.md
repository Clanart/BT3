Based on the evidence gathered, I found a partial structural analog but could not fully verify all supporting details (e.g. I was unable to load `runtime/src/bank/fee_distribution.rs` to confirm whether the block-revenue commission path has the identical inconsistency, and I did not find explicit design-rationale comments explaining why only `commission_bps` — and not the collector pubkey — is delayed). With that caveat, here is the strongest analog I found from local code.

### Title
Inflation-rewards commission recipient (`inflation_rewards_collector`) is not delayed like commission rate, allowing a vote-account authority change to redirect already-earned validator commission - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The DeliHook bug's broken invariant is: *a value that was earned/attributed under a previous owner is paid out based on the owner recorded at settlement time, not the owner at the time the value was earned, because the ownership change is not delayed relative to the payout logic.* Agave's epoch-rewards code contains the same class of gap for validator commission payouts: the commission **rate** is intentionally delayed by a full epoch to stop "last-minute commission rugs," but the commission **recipient address** (`inflation_rewards_collector`) used at distribution time is *not* delayed and is read from the live/current vote account state.

### Finding Description
In `redeem_delegation_rewards` [1](#0-0) , the commission rate is deliberately sourced from a snapshotted vote-account view when `delay_commission_updates` is active:

```
let commission_bps = if delay_commission_updates {
    let vote_state_for_commission = snapshot_epoch_vote_accounts
        .and_then(|eva| eva.get(&vote_pubkey))
        .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
        ...
``` [2](#0-1) 

This snapshotting exists explicitly "to prevent last minute commission rugs," as documented on the `CachedVoteAccounts` struct [3](#0-2) .

However, immediately afterward, the **destination pubkey** for that same commission is computed from `vote_state`, which was obtained from `distribution_epoch_vote_accounts` (the *current*, non-delayed vote account view) at line 701:

```
let (commission_pubkey, is_vote_account) = if custom_commission_collector {
    let commission_pubkey = *vote_state
        .inflation_rewards_collector()
        .unwrap_or(&vote_pubkey);
    (commission_pubkey, commission_pubkey == vote_pubkey)
} else {
    (vote_pubkey, true)
};
``` [4](#0-3) 

The collector address is set via the `UpdateCommissionCollector` instruction (SIMD-0232), gated only by requiring the current authorized withdrawer's signature — no delay or epoch-boundary restriction is applied: [5](#0-4) , dispatched from `vote_processor.rs` without any commission-style delay check [6](#0-5) .

This mirrors the DeliHook root cause exactly: one piece of "who gets paid" state (the rate) is protected against last-second manipulation, while the other piece of "who gets paid" state (the destination address) is not, even though both determine the same payout. The reward-calculation pass (`calculate_stake_rewards_and_commissions`) already carries both `delay_commission_updates` and `custom_commission_collector` as sibling feature flags into `redeem_delegation_rewards` [7](#0-6) , showing the two features were combined without extending the delay logic to cover the collector field.

### Impact Explanation
Whoever holds the authorized-withdrawer key for a vote account at the moment of the (potentially multi-block, partitioned) epoch-reward distribution — rather than whoever held it, or configured the collector, during the epoch in which the commission was actually earned — receives that epoch's commission lamports. In scenarios where control of a vote account changes hands (e.g. a validator business sale, key rotation to a new operator, or a compromised withdrawer key), the incoming/attacking party can call `UpdateCommissionCollector` after the reward-earning epoch has closed but before distribution executes, redirecting commission lamports that were economically earned under the prior configuration. This is a fund-diversion primitive limited to commission lamports (bounded by the vote account's own commission), not a broader consensus or theft-from-delegators bug, so the blast radius is narrower than the original DeliHook report.

### Likelihood Explanation
Requires: (1) `custom_commission_collector` and (ideally) `delay_commission_updates`/`commission_rate_in_basis_points` features active, (2) control of the vote account's authorized withdrawer key changing hands or being briefly held by an attacker, and (3) timing the `UpdateCommissionCollector` call within the window between epoch-boundary and the (potentially delayed/partitioned) reward distribution. This is a narrow, authority-gated window rather than a fully unprivileged remote attack, which is why I present this with reduced confidence relative to a typical "no privilege needed" Agave finding.

### Recommendation
Extend the same delay/snapshot mechanism used for `commission_bps` to `inflation_rewards_collector` (and the analogous `block_revenue_collector`, if the same pattern exists in `runtime/src/bank/fee_distribution.rs`, which I could not fully verify): read the collector pubkey from `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` rather than the live `distribution_epoch_vote_accounts` view whenever `delay_commission_updates` is enabled, so commission rate and commission destination are protected consistently.

### Proof of Concept
Conceptual (not executed, no test harness run in this session):
1. Enable `custom_commission_collector`, `delay_commission_updates`, `commission_rate_in_basis_points` features; create a vote account `V` with `inflation_rewards_collector = A`, delegated stake earning commission over epoch `N`.
2. At the end of epoch `N` (after the rate has already been snapshotted for distribution, but before `distribute_epoch_rewards_in_partition` actually pays out `V`'s commission — distribution can be split across partitions/blocks per `store_stake_accounts_in_partition` [8](#0-7) ), the withdrawer authority (e.g., a new buyer of the vote account) issues `UpdateCommissionCollector(InflationRewards)` changing the collector from `A` to `B`.
3. When distribution executes, `redeem_delegation_rewards` uses the delayed/snapshotted `commission_bps` (correct rate for epoch `N`) but reads `vote_state.inflation_rewards_collector()` from the current state, yielding `B` instead of `A` [4](#0-3) .
4. `B` receives commission lamports economically earned while `A` was configured as collector.

Given the caveats above (unverified interaction with `fee_distribution.rs`'s block-revenue path, and the requirement that the attacker/beneficiary controls the withdrawer key rather than being fully unprivileged), this should be treated as a moderate-confidence structural analog rather than a fully confirmed critical vulnerability — I'd recommend a Devin session with full repo/test access to confirm distribution-timing windows and check `fee_distribution.rs` before treating this as final.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L700-724)
```rust
        };
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L750-757)
```rust
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L793-849)
```rust
        let feature_snapshot = self.feature_set.snapshot();
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
        let delay_commission_updates = feature_snapshot.delay_commission_updates;
        let commission_rate_in_basis_points = feature_snapshot.commission_rate_in_basis_points;
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let block_revenue_sharing = feature_snapshot.block_revenue_sharing;

        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
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

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-311)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
```

**File:** programs/vote/src/vote_state/mod.rs (L907-933)
```rust
/// Update the vote account's commission collector (SIMD-0232).
pub fn update_commission_collector<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    new_collector: NewCommissionCollector,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    rent: &Rent,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let new_collector_key = new_collector.validate_and_resolve_key(vote_account, rent)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_collector(new_collector_key);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_collector(new_collector_key);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_processor.rs (L383-408)
```rust
        VoteInstruction::UpdateCommissionCollector(kind) => {
            // SIMD-0232: Custom Commission Collector Account
            // Requires SIMD-0185: Vote State V4
            let custom_collector_enabled =
                invoke_context.get_feature_set().custom_commission_collector;
            if !custom_collector_enabled {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(3)?;
            let new_collector = read_new_collector_account(&instruction_context, &me, 1)?;

            let rent = invoke_context
                .environment_config
                .sysvar_cache()
                .get_rent()?;

            vote_state::update_commission_collector(
                &mut me,
                target_version,
                new_collector,
                kind,
                &signers,
                &rent,
            )
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-360)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
```
