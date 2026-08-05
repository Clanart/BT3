### Title
Block-revenue-sharing reward pool (`pending_delegator_rewards`) is distributed pro-rata by *current* stake share with no per-epoch attribution, letting newly-delegated stake dilute rewards earned by long-term delegators - (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report describes an ERC4626 vault whose reward-vesting mechanism blends unvested rewards into `totalAssets()`, letting a depositor who arrives *after* rewards started accruing still claim a slice of them at redemption, diluting earlier holders. Agave's SIMD-0123 block-revenue-sharing mechanism has the same class of flaw: it accumulates a single unattributed reward pool (`pending_delegator_rewards`) in the vote account and, once per epoch, pays it out strictly proportional to *current* effective stake share — with no bookkeeping of which stake was actually delegated while each lamport of that pool was earned/deposited.

### Finding Description
`VoteStateV4::pending_delegator_rewards` is a single scalar balance that grows via `deposit_delegator_rewards` (`programs/vote/src/vote_state/mod.rs:936-988`), which simply does `pending_delegator_rewards = pending_delegator_rewards.checked_add(deposit)` [1](#0-0) . There is no timestamp, epoch marker, or per-deposit ledger recorded — it is a flat pool, exactly like the un-vested/vested blended `totalAssets()` value in the sPinto report.

At each epoch's reward calculation, `calculate_block_reward` reads this *entire* pool and divides it among all current delegators strictly by their present stake share:
```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
``` [2](#0-1) 

`stake` here is the delegator's `delegation_effective_stake` for `rewarded_epoch` [3](#0-2) , and `total_active_stake` is `reward_epoch_delegated_stakes.delegated_stakes` for the vote account [4](#0-3) . This is called for every stake delegation during `calculate_stake_rewards_and_commissions`, guarded only by the `block_revenue_sharing` feature flag [5](#0-4) .

Contrast this with the pre-existing, correctly-designed inflation-reward ("tower points") mechanism, which is deliberately per-epoch and per-credit: `tower_epoch_credits_iter` walks each epoch's `epoch_credits` entry and multiplies the *stake effective in that specific epoch* by the credits earned in that specific epoch, so a delegator's `credits_observed` watermark prevents them from claiming credit for epochs before they staked [6](#0-5) . The block-revenue pool has no analogous "observed" watermark per epoch of accrual — it only tracks a single running total, so once a delegation is fully warmed-up and counted in `total_active_stake` for `rewarded_epoch`, it receives its full pro-rata share of *however many epochs' worth* of deposits have accumulated in `pending_delegator_rewards`, even lamports that were deposited into the vote account before that delegator's stake existed at all.

I was unable to locate, within the reward-distribution code path (`distribute_epoch_rewards_in_partition` / `store_stake_accounts_in_partition` / `build_updated_stake_reward`), any code that decrements the vote account's `pending_delegator_rewards` field after a distribution round [7](#0-6) . Those functions only mutate the *stake* account's lamports/delegation, not the vote account's `pending_delegator_rewards` bookkeeping value. If this field is in fact never reduced elsewhere, the pool would compound in size across epochs (which would make the described dilution larger and multi-epoch), rather than being fully drained and reset each epoch. This detail could not be confirmed with the tools available and should be verified directly against the vote-account mutation code that runs during `load_and_reward_commission_accounts`/reward distribution.

### Impact Explanation
This causes value to be misallocated among unprivileged stakers: a delegator who moves new stake onto a validator that has accumulated a large `pending_delegator_rewards` balance (from block revenue deposited over one or more prior epochs) captures a pro-rata share of that historical pool as soon as their stake counts toward `total_active_stake` for a `rewarded_epoch` — diluting the payout that should have accrued to delegators who were staked throughout the accrual period. This is fund misallocation between unprivileged parties (existing vs. newly arriving delegators to the same validator), matching the "future depositors receive a portion of the reward that began to be distributed before their deposit" bug class in the report.

### Likelihood Explanation
Exploitability is bounded by Solana's stake warm-up delay (a new/increased delegation only becomes "effective" starting the epoch after `activation_epoch`, per `delegation_effective_stake`/`stake_activating_and_deactivating` [8](#0-7) ), so an attacker must lock capital for roughly one epoch before their stake is even counted in `total_active_stake`. This is the same kind of "must hold capital across the vesting period" mitigation the report itself calls out for PintoFarm and treats as merely risk-reducing rather than eliminating the underlying flaw. It does not eliminate the dilution for a delegator who is willing to accept normal staking risk for one epoch, and does not require any malicious/trusted party — only a validator operator calling the permissionless `DepositDelegatorRewards` instruction and ordinary delegators moving stake, which is exactly the kind of unprivileged transaction-level behavior in scope.

### Recommendation
Track block-revenue-pool accrual on a per-epoch basis (similar to the `epoch_credits` / `credits_observed` mechanism used for inflation rewards) instead of a single flat `pending_delegator_rewards` scalar, so that a delegator's share of block-revenue rewards is computed only over the epochs in which their stake was actually effective, rather than pro-rata over the entire pool balance at distribution time. At minimum, confirm and, if missing, add logic to fully drain/reset `pending_delegator_rewards` to zero synchronously with each distribution so the accrual window can never exceed a single epoch, bounding (though not eliminating) the dilution window.

### Proof of Concept
1. Validator V has `block_revenue_sharing` enabled and has accumulated `pending_delegator_rewards = P` in its `VoteStateV4` over epoch `E-1` from block-revenue deposits, with total active delegated stake `S` (from long-standing delegators).
2. Near the end of epoch `E-1`, an attacker delegates a large stake amount `X` to V (or increases an existing delegation).
3. The stake activates during warm-up so that by `rewarded_epoch = E-1` (per `delegation_effective_stake`) it counts in `reward_epoch_delegated_stakes.delegated_stakes[V]`, making `total_active_stake = S + X`.
4. During epoch `E`'s reward calculation, `calculate_block_reward` pays the attacker `P * X / (S + X)` [9](#0-8) , even though `P` was deposited into V's vote account before the attacker's stake `X` existed.
5. The long-standing delegators collectively receive only `P * S / (S + X)` instead of the full `P` they would have received absent the newcomer, demonstrating the dilution described in the report's bug class.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L196-208)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-210)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L211-231)
```rust
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

**File:** runtime/src/inflation_rewards/points.rs (L187-233)
```rust
fn tower_epoch_credits_iter(
    stake: &Stake,
    epoch_credits_iter: impl Iterator<Item = (Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> (u128, u64, bool) {
    let mut points = 0;
    let credits_in_stake = stake.credits_observed;
    let mut new_credits_observed = credits_in_stake;
    let mut saw_marker = false;

    for entry in epoch_credits_iter {
        if entry == AG_MIGRATION_EPOCH_CREDIT {
            saw_marker = true;
            break;
        }
        let (epoch, final_epoch_credits, initial_epoch_credits) = entry;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));

        // finally calculate points for this epoch
        let earned_points = stake_amount * earned_credits;
        points += earned_points;

        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CalculatedPoints(
                epoch,
                stake_amount,
                earned_credits,
                earned_points,
            ));
        }
    }
    (points, new_credits_observed, saw_marker)
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L239-325)
```rust
    fn build_updated_stake_reward(
        distribution_epoch: u64,
        stake_history: &StakeHistory,
        new_warmup_cooldown_rate_epoch: Option<Epoch>,
        stakes_cache_accounts: &imbl::HashMap<Pubkey, StakeAccount<Delegation>>,
        partitioned_stake_reward: &PartitionedStakeReward,
        rent: &Rent,
        adjust_delegations_for_rent: bool,
        use_fixed_point_stake_math: bool,
    ) -> Result<StakeReward, DistributionError> {
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;

        let stake_at_distribution_epoch = delegation_effective_stake(
            &new_stake.delegation,
            distribution_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        let reward_type = if stake_at_distribution_epoch == 0 {
            RewardType::DeactivatedStake
        } else {
            RewardType::Staking
        };
        Ok(StakeReward {
            stake_pubkey: partitioned_stake_reward.stake_pubkey,
            stake_reward_info: StakeRewardInfo {
                reward_type,
                lamports: i64::try_from(
                    partitioned_stake_reward.inflation.stake_reward
                        + partitioned_stake_reward.block_reward,
                )
                .unwrap(),
                post_balance: account.lamports(),
                commission_bps: partitioned_stake_reward.inflation.commission_bps,
            },
            stake_account: account,
        })
    }
```

**File:** runtime/src/stake_delegation.rs (L9-23)
```rust
#[inline]
pub(crate) fn delegation_effective_stake<T: StakeHistoryGetEntry>(
    delegation: &Delegation,
    epoch: Epoch,
    history: &T,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    if use_fixed_point_stake_math {
        delegation.stake_v2(epoch, history, new_rate_activation_epoch)
    } else {
        #[allow(deprecated)]
        delegation.stake(epoch, history, new_rate_activation_epoch)
    }
}
```
