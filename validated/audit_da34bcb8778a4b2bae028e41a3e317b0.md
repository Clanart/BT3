Audit Report

## Title
`pending_delegator_rewards` in `VoteStateV4` is never decremented after block-reward distribution, causing the same deposited SOL to be recomputed and repaid to stakers every epoch - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

## Summary
`calculate_block_reward()` computes each stake account's `block_reward` as a proportional share of the vote account's `pending_delegator_rewards` field [1](#0-0) , and that reward is credited to stake accounts every epoch in `store_stake_accounts_in_partition`/`build_updated_stake_reward` [2](#0-1) . No code path decrements `pending_delegator_rewards` or debits the vote account's actual lamports after this payout; the only writer of the field is `add_pending_delegator_rewards()`, called exclusively from the permissionless `DepositDelegatorRewards` instruction [3](#0-2) . Consequently the same deposited balance is read and paid out again every subsequent epoch that a delegation remains active, while `distribute_epoch_rewards_in_partition` only adjusts capitalization for `stake_reward_lamports_minted`/`block_reward_lamports_burned` — never for `block_reward_lamports_distributed` — confirming that the design intends block rewards to be a one-time drawdown from vote-account lamports that in practice is never enforced [4](#0-3) .

## Finding Description
`calculate_block_reward()` reads `vote_state.pending_delegator_rewards()` and computes `(pending_delegator_rewards * stake / total_active_stake).min(pending_delegator_rewards)` as the `block_reward` for each stake delegation [5](#0-4) . This function is invoked once per epoch per delegation whenever `block_revenue_sharing` is enabled, from within `calculate_stake_rewards_and_commissions()` [6](#0-5) , and the resulting value is stored per `PartitionedStakeReward` and later credited directly to the stake account's lamports via `checked_add_lamports` during distribution [2](#0-1) .

Tracing every write site of `pending_delegator_rewards`: the only mutator found is `add_pending_delegator_rewards()`, an increment-only operation invoked from `deposit_delegator_rewards()` (backing the permissionless `DepositDelegatorRewards` instruction) [3](#0-2) [7](#0-6) . Neither `store_stake_accounts_in_partition`, `build_updated_stake_reward`, nor `distribute_epoch_rewards_in_partition` touches the vote account at all — they only read from `distribution_epoch_vote_accounts`/`stakes_cache_accounts` and write to stake accounts [8](#0-7) . There is no `checked_sub_lamports` on any vote account and no decrement of `pending_delegator_rewards` anywhere in this pipeline. The only place the field is zeroed is test setup code for an unrelated `Withdraw` test [9](#0-8) .

Critically, `distribute_epoch_rewards_in_partition` explicitly adds `stake_reward_lamports_minted` to capitalization (new inflation issuance) and subtracts `block_reward_lamports_burned` (failed distributions), but never adds `block_reward_lamports_distributed` [4](#0-3) . This confirms the intended design: block rewards are meant to be transferred from already-existing, pre-funded vote-account lamports (deposited via `DepositDelegatorRewards`, itself a capitalization-neutral system transfer) to stake accounts, capitalization-neutral. But because the vote account is never actually debited and `pending_delegator_rewards` is never reduced, each epoch's payout is effectively minted out of nothing: total lamports across all accounts increases (stake accounts gain lamports) while capitalization tracking does not reflect this and the source vote account's balance/state remains unchanged, so the same deposit is redistributed indefinitely.

## Impact Explanation
This causes an unbounded, indefinitely repeating fund-creation bug: every epoch boundary that `block_revenue_sharing` is active and a delegation remains active, stakers receive lamports credited via `checked_add_lamports` that are not matched by any corresponding debit from the vote account nor by a capitalization increase. This is a fund-accounting-correctness violation of the exact value `pending_delegator_rewards` (VoteStateV4 field) — it never shrinks despite backing repeated real lamport transfers to stake accounts — and produces a genuine divergence between total actual lamports in the ledger and the tracked `capitalization` value. Because reward calculation and distribution are core, mandatory bank operations executed identically by all validators from the same on-chain state, this is a deterministic, network-wide effect (not merely a single-node bug), and repeated indefinitely it would cause real, uncapped lamport inflation for stakers of any vote account that ever received a `DepositDelegatorRewards` call.

## Likelihood Explanation
No privileged actor or crafted malicious input is required. `DepositDelegatorRewards` is a permissionless instruction any signer/depositor can invoke once [7](#0-6) , and afterward `calculate_stake_rewards_and_commissions()` / `calculate_block_reward()` run automatically and unconditionally as part of every epoch's core reward computation for every bank once `block_revenue_sharing`, `custom_commission_collector`, and `commission_rate_in_basis_points` features are active [6](#0-5) . This guarantees the missing-decrement path is exercised every epoch with no further attacker action needed, making it fully deterministic and repeatable.

## Recommendation
When `block_reward` is computed and subsequently credited to stake accounts, atomically debit the same amount from the source vote account's lamports and subtract it from `pending_delegator_rewards` (e.g., in `store_stake_accounts_in_partition`/`build_updated_stake_reward`, alongside crediting `checked_add_lamports` on the stake account). Alternatively, if the design intends block rewards to be newly minted rather than drawn from vote-account deposits, `block_reward_lamports_distributed` must be explicitly added to `capitalization` in `distribute_epoch_rewards_in_partition`, matching the treatment already given to `stake_reward_lamports_minted`.

## Proof of Concept
1. Enable `block_revenue_sharing`, `custom_commission_collector`, and `commission_rate_in_basis_points` features; call `DepositDelegatorRewards` on a vote account with `deposit = X`, setting `pending_delegator_rewards = X` [3](#0-2) .
2. At epoch N boundary, `calculate_block_reward()` computes and credits `block_reward = X * stake/total_active_stake` per delegation to stake accounts [10](#0-9) [2](#0-1) ; `pending_delegator_rewards` remains `X` unchanged on-chain, and the vote account's actual lamports remain unchanged.
3. At epoch N+1 boundary, `calculate_block_reward()` runs again against the still-unchanged `pending_delegator_rewards = X`, recomputing and crediting an equivalent `block_reward` a second time with no new deposit.
4. Repeat for arbitrary epochs; write a Rust integration test asserting that after two consecutive reward epochs (same vote account, no new `DepositDelegatorRewards` call), the total lamports credited to delegated stake accounts via `block_reward` exceeds the originally deposited `X`, while `capitalization` and the vote account's lamports/`pending_delegator_rewards` are unchanged — demonstrating uncontrolled fund creation.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L174-231)
```rust
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L801-833)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-198)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-423)
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
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
        }
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
        DistributionResults {
            stake_reward_lamports_minted,
            stake_reward_lamports_burned,
            block_reward_lamports_distributed,
            block_reward_lamports_burned,
            updated_stake_rewards,
        }
    }
```

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

**File:** programs/vote/src/vote_processor.rs (L409-426)
```rust
        VoteInstruction::DepositDelegatorRewards { deposit } => {
            // SIMD-0123: Deposit delegator rewards.
            // Requires:
            // * SIMD-0185: Vote State V4
            // * SIMD-0291: Commission in Basis Points
            // * SIMD-0232: Custom Commission Collector
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points
                || !feature_set.custom_commission_collector
                || !feature_set.block_revenue_sharing
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(2)?;
            drop(me);
            vote_state::deposit_delegator_rewards(invoke_context, 0, 1, deposit, &signers)
        }
```

**File:** programs/vote/src/vote_processor.rs (L5304-5311)
```rust
        // Now clear pending delegator rewards.
        {
            let mut vote_state =
                VoteStateV4::deserialize(vote_account.data(), &vote_pubkey).unwrap();
            vote_state.pending_delegator_rewards = 0;
            vote_account.set_data_from_slice(&VoteStateHandler::new_v4(vote_state).serialize());
            vote_account.set_lamports(vote_account_lamports);
        };
```
