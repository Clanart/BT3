## Title
Custom commission collector address is not epoch-delayed, letting the withdraw authority redirect already-earned inflation-reward commissions to a new collector — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

## Summary
Agave's vote program lets a vote account's authorized withdrawer redesignate the *inflation-rewards commission collector* pubkey via `update_commission_collector` at any time, with no epoch-delay lock analogous to the one enforced on the commission *rate*. When rewards for the just-finished epoch are calculated, the *rate* is looked up from an earlier ("delayed") snapshot of the vote account to stop last-minute rate hikes, but the *collector pubkey* is read from the vote account's current/distribution-time state. This is the same broken-invariant shape as the reported `PermissionlessPaymaster.selfRevokeSigner()` bug: a "who gets the payout" field is overwritten with a "live" value instead of the value that was in effect when the funds were actually earned, letting whoever currently controls the account divert funds that accrued under a different party.

## Finding Description
`update_commission_collector` (SIMD-0232) lets the authorized withdrawer change `inflation_rewards_collector` on a `VoteStateV4` account with only a signer check — no timing restriction: [1](#0-0) 

By contrast, commission-rate changes are explicitly protected from being applied to rewards that were already earned. `update_commission_bps` removed the on-chain slot-based lock (per SIMD-0249/SIMD-0291) but this is only safe because the *reward calculation* code independently "delays" the rate by reading it from an earlier vote-account snapshot instead of the live one: [2](#0-1) 

In the reward-calculation path, `redeem_delegation_rewards` implements that delay for `commission_bps` only: [3](#0-2) 

But the commission **collector pubkey** — the account that actually receives the commission lamports — is taken from `vote_state`, i.e. from `distribution_epoch_vote_accounts` (the *current*, non-delayed view of the vote account, taken at reward-distribution time), not from `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` like the rate is: [4](#0-3) 

So the code explicitly protects the commission *rate* against retroactive tampering but forgot to apply the same protection to the commission *recipient address*. This is structurally identical to the reported bug: `selfRevokeSigner()` correctly avoided leaking funds to a revoked signer for its own state, but stomped the wrong "who gets paid" variable with a live value instead of the historically-correct one. Here, Agave stomps the "who gets paid" field (`inflation_rewards_collector`) with the live value at distribution time instead of the value that was current during the epoch the rewards were earned in.

## Impact Explanation
An authorized withdrawer (or, in an SIMD-0232 custom-collector delegation-as-a-service arrangement, a person who was supposed to only route the collector-designation authority to a legitimate third party, e.g. a stake pool or delegator payout account) can call `UpdateCommissionCollector` after the epoch's stake/vote credits have already been earned (i.e., after epoch N's rewards are already fixed by validator performance) but before the partitioned reward-commission distribution for epoch N executes at the start of epoch N+1, and redirect the entire epoch's inflation-reward commission to an address they control instead of the address that was authoritative during epoch N. This results in direct fund theft/misdirection of validator commission lamports — the exact "steal all gas refunds"-class impact described in the report, translated to "steal all inflation-reward commission for an already-completed epoch."

## Likelihood Explanation
Likelihood is limited by the narrow timing window: the withdrawer must submit `UpdateCommissionCollector` after credits for epoch N are locked in (unavoidable, since credits are earned throughout the epoch) but before the bank actually runs `calculate_stake_rewards_and_commissions`/`redeem_delegation_rewards` for epoch N, which snapshots `distribution_epoch_vote_accounts`. Because commission *rate* changes are explicitly delayed by one epoch to prevent exactly this class of "front-run distribution" attack, and only the collector field escaped that protection, this looks like an unintentional asymmetry introduced when SIMD-0232 (custom collector) was layered on top of SIMD-0249/0291 (delayed commission updates) rather than a deliberately accepted risk — making it a real, currently valid path, not merely a theoretical one, requiring no malicious peer/validator collusion, only an authorized withdrawer acting unilaterally (which is the "unprivileged w.r.t. other stakeholders/delegators" actor the report's bug class targets).

## Recommendation
Apply the same delay used for `inflation_rewards_commission_bps` to `inflation_rewards_collector`: when `delay_commission_updates` (or the custom-collector equivalent) is active, resolve the commission recipient from `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` (the same delayed source used for `commission_bps`) instead of from `distribution_epoch_vote_accounts`. Concretely, in `redeem_delegation_rewards` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), compute `commission_pubkey` using `vote_state_for_commission` (the delayed view already computed for `commission_bps`) rather than the live `vote_state`.

## Proof of Concept
1. Vote account `V` has `authorized_withdrawer = W` and `inflation_rewards_collector = A` (e.g., a stake-pool treasury) for the duration of epoch `N`. Delegated stake earns credits/inflation rewards throughout epoch `N` with `A` as the entitled collector.
2. At/just after the epoch boundary into epoch `N+1`, but before the bank has run `calculate_stake_rewards_and_commissions` for the rewarded epoch `N` (partitioned rewards are computed/distributed over the early blocks of the new epoch), `W` submits `VoteInstruction::UpdateCommissionCollector { new_collector: B, kind: InflationRewards }`, processed by `update_commission_collector`: [5](#0-4) 
   This succeeds immediately — there is no `is_commission_update_allowed`-style slot check for the collector, unlike the legacy rate path.
3. When the bank executes `redeem_delegation_rewards` for the rewarded epoch `N`, it reads `commission_bps` from the *delayed* snapshot (correctly preserving the rate that was active during epoch `N`), but reads `commission_pubkey` from the *current* `vote_state` (now `B`, not `A`): [6](#0-5) 
4. All of epoch `N`'s inflation-reward commission lamports — earned while `A` was the designated collector — are paid out to `B` instead of `A`, verifiable via `store_commission_accounts_partitioned`/`distribute_reward_commissions`: [7](#0-6) 

This can be scripted as an integration test analogous to the existing `test_calculate_stake_vote_rewards` test harness (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`), by inserting an `UpdateCommissionCollector` transaction between epoch-credit accrual and the bank's reward-calculation call, and asserting the resulting `reward_commissions` map key is the new collector `B` rather than the collector `A` that was in effect during the rewarded epoch.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L353-390)
```rust
    pub(in crate::bank) fn distribute_reward_commissions(
        &mut self,
        prev_epoch: Epoch,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> RewardCommissionLamportAmounts {
        let PartitionedRewardsCalculation {
            reward_commissions,
            stake_rewards,
            capitalization,
            point_value,
            num_filtered_vote_accounts,
            ..
        } = rewards_calculation;

        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
        rewards_metrics.load_and_reward_commission_accounts_us =
            load_and_reward_commission_accounts_us;
        info!(
            "load_and_reward_commission_accounts: input_count={} output_count={} elapsed_us={}",
            reward_commissions.len(),
            reward_commission_accounts.accounts_with_rewards.len(),
            load_and_reward_commission_accounts_us,
        );

        let RewardCommissionLamportAmounts {
            distributed_lamports,
            distributed_to_incinerator_lamports,
            burned_lamports,
        } = reward_commission_accounts.amounts;
        self.store_commission_accounts_partitioned(&reward_commission_accounts, rewards_metrics);
        self.update_reward_commissions(&reward_commission_accounts);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L701-768)
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

        match redeem_rewards(
            stake,
            commission_bps,
            DelegatedVoteState::from(vote_state),
            CalculationEnvironment {
                rewarded_epoch,
                point_value,
                stake_history,
                new_rate_activation_epoch,
                commission_rate_in_basis_points,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            },
            reward_calc_tracer,
            ag_epoch_type,
            current_lamports,
            minimum_lamports,
        ) {
            Ok((stake_reward, commission_lamports, stake)) => {
                let inflation = InflationReward {
                    stake,
                    stake_reward,
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
                let reward_commission = RewardCommission {
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                    commission_lamports,
                    burned_lamports: 0,
                    is_vote_account,
                };
                Some(InflationRewardWithCommission {
                    inflation,
                    commission_pubkey,
                    reward_commission,
                })
```
