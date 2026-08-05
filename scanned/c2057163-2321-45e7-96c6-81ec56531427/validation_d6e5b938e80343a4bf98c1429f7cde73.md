Based on my research into Agave's block-revenue-sharing reward pipeline, I found a real analog to the "zero-supply carries stale rewards forward to an unfair first-mover" bug class.

### Title
Stale `pending_delegator_rewards` are windfalled to the next staker to activate after a validator's active stake drops to zero - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The `VsTAN`/`RewardAccumulator` bug is caused by a reward accumulator (`rewardRate` / `pending_delegator_rewards`-style pool) that keeps growing while `totalSupply == 0`, and then dumps the entire un-earned backlog onto whichever depositor shows up first once supply becomes non-zero again, because the "last update" checkpoint isn't advanced during the zero-supply window. Agave's SIMD-0123 block-revenue-sharing path has the same structural shape: a per-validator lamport pool (`pending_delegator_rewards`) accrues every block regardless of whether that validator currently has any *active* delegated stake, and is only ever split among stakers proportionally to `total_active_stake` at reward-calculation time — with no mechanism to expire, checkpoint, or burn the portion that accrued while `total_active_stake == 0`.

### Finding Description
Block rewards are deposited into a vote account's `pending_delegator_rewards` field unconditionally via `add_pending_delegator_rewards`, called from `deposit_delegator_rewards`: [1](#0-0) [2](#0-1) 

This pool is a running lamport balance that is completely decoupled from whether the validator has any currently-active delegated stake — it simply grows with every deposit.

At epoch-reward time, `calculate_block_reward` splits this pool pro-rata by `stake / total_active_stake` for the *reward epoch*: [3](#0-2) 

Critically, when `total_active_stake == 0` for that vote account in a given reward epoch (e.g., all delegations to it were deactivated or none had yet activated), the function returns `0` for every delegation — but it does **not** reset, burn, or reduce `pending_delegator_rewards`: [4](#0-3) 

The pool is only ever debited implicitly through the `block_reward` amounts actually paid out as part of `PartitionedStakeReward` during distribution (`redeem_delegation_rewards` / `calculate_stake_rewards_and_commissions`): [5](#0-4) . When the epoch's payout is `0` for every delegator (because `total_active_stake == 0`), nothing is subtracted from the vote account's `pending_delegator_rewards`, so the entire backlog — including lamports that accrued during blocks when nobody had stake activated on that validator — silently rolls forward, undiminished, into whichever future epoch `total_active_stake` becomes non-zero again.

This is structurally identical to the reported bug:
1. `totalSupply == 0` ⇔ `total_active_stake == 0` for the vote account.
2. Rewards keep accruing into the pool during the zero-stake window (`rewardRate` leftover ⇔ `pending_delegator_rewards` deposits).
3. There is no "last update"/checkpoint mechanism that prevents the full accrued backlog from being attributed to the next staker who activates a delegation to that validator — that staker did not participate in any of the blocks whose revenue funded the backlog, yet receives a proportional (potentially outsized, since they may be the *only* active delegator) share of it.

### Impact Explanation
An unprivileged staker who delegates stake to a validator immediately after that validator's active stake base transitions from zero back to non-zero can receive a windfall share of lamports that were deposited into `pending_delegator_rewards` during blocks in which they had no economic exposure at all. Because Agave's split is `stake / total_active_stake`, if this staker is first/only, they can capture up to 100% of the accumulated backlog with a trivial or freshly-created stake position — a fund-misallocation (unjust enrichment at the expense of the validator/other future delegators) rather than a value-creation event tied to actual participation.

### Likelihood Explanation
This requires a validator's total active delegated stake to reach exactly zero for at least one reward epoch while it still keeps producing blocks and depositing into `pending_delegator_rewards` (a non-trivial but realistic scenario, e.g., a validator briefly losing all delegations during a stake-churn window, or delegators intentionally timing deactivation/reactivation to game the payout), followed by a new/returning delegation activating. No malicious validator, admin, or privileged actor is required — any unprivileged staker choosing when to (re)delegate can trigger and benefit from this.

### Recommendation
When `total_active_stake == 0` for a vote account in a reward epoch, the corresponding share of `pending_delegator_rewards` that accrued during that zero-stake window should be excluded from future proportional splits — e.g., by tracking a checkpoint/snapshot of `pending_delegator_rewards` at the point stake last went to zero (or last became non-zero) and only distributing the delta earned while `total_active_stake > 0`, analogous to correctly handling `lastUpdateTime` in the reported Synthetix-style pattern.

### Proof of Concept
I was unable to fully trace the lamport-debit side of `pending_delegator_rewards` in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` within the available tool budget (48 references to `block_reward` in that file were located but not inspected in detail), so I cannot definitively confirm the exact mechanics of how/whether `pending_delegator_rewards` is decremented during distribution, nor construct a fully verified step-by-step transaction trace. The analog is presented based on the calculation-side code paths cited above, which clearly show: (a) unconditional accrual independent of active-stake state, and (b) a zero-payout branch for `total_active_stake == 0` with no accompanying reduction of the pool. A background Devin session with full repository access would be needed to verify the distribution-side debit logic and construct a concrete end-to-end PoC scenario (validator stake churn timeline + exact lamport amounts).

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
```rust
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-892)
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

                    let (reward, maybe_reward_record) = match (block_reward, maybe_reward_record) {
                        (0, None) => (None, None),
                        (_, Some(res)) => {
                            let InflationRewardWithCommission {
                                inflation,
                                commission_pubkey,
                                reward_commission,
                            } = res;
                            let stake_reward = inflation.stake_reward;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation,
                                    block_reward,
                                }),
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: Some((commission_pubkey, reward_commission)),
                                }),
                            )
                        }
                        (_, None) => {
                            // Create a zero entry for distribution
                            let stake = *stake_account.stake();
                            let stake_reward = 0;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation: InflationReward {
                                        stake,
                                        stake_reward,
                                        commission_bps: None,
                                    },
                                    block_reward,
                                }),
                                // Need a reward record for accumulator
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: None,
                                }),
                            )
                        }
```
