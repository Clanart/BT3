Based on my investigation, I found a strong structural analog to the H-06 pattern in the SIMD-0123 block-reward-sharing code path.

### Title
`pending_delegator_rewards` on vote accounts is read and paid out every epoch but never decremented, allowing block-reward re-distribution — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The H-06 report's broken invariant is: a reward balance is read and paid out to a claimant, but the balance field itself is never reduced, so the same reward can be paid repeatedly. In Agave's SIMD-0123 "block revenue sharing" feature, `VoteStateV4::pending_delegator_rewards` plays the same role as `reward[msg.sender]` in the Mochi bug: it is deposited via `deposit_delegator_rewards` [1](#0-0) , incremented with `add_pending_delegator_rewards` [2](#0-1) , and then read every epoch by `calculate_block_reward` to compute each delegator's proportional share of the pool [3](#0-2) .

### Finding Description
`calculate_block_reward` computes, per stake delegation, `pending_delegator_rewards * stake / total_active_stake` (capped at `pending_delegator_rewards`) [4](#0-3) . This value is stored as `block_reward` on a `PartitionedStakeReward` and later credited directly to the stake account's lamports via `checked_add_lamports` in `build_updated_stake_reward` [5](#0-4) .

Across the entire reward-commission/distribution pipeline (`distribute_reward_commissions`, `begin_partitioned_rewards`, `store_stake_accounts_in_partition`, `distribute_epoch_rewards_in_partition`), I could not find any corresponding write that reduces `VoteStateV4::pending_delegator_rewards` or debits the vote account's lamports by the paid-out `block_reward` amount. The only mutator of this field found in the codebase is `add_pending_delegator_rewards` (increment-only) [2](#0-1) ; there is no `sub_pending_delegator_rewards` or equivalent anywhere in `programs/vote/` or `runtime/src/bank/`.

This mirrors the Mochi bug precisely: `claimRewardAsMochi` read `reward[msg.sender]` and paid it out without `reward[msg.sender] = 0`, so the same reward could be claimed on every subsequent call. Here, `calculate_block_reward` reads `pending_delegator_rewards` and pays a proportional share to every staker on every epoch boundary where `block_revenue_sharing` is active, without ever reducing the pool value — so the exact same pool of deposited lamports (and the vote account's lamports, which back the `checked_add_lamports` calls into stake accounts) would be re-distributed again the following epoch, without the vote account's balance being drawn down to match.

### Impact Explanation
If confirmed, this would let the same deposited "block revenue" (`pending_delegator_rewards`) be paid out to delegators repeatedly across multiple epochs from a single deposit, effectively minting lamports that exist in stake accounts but were never debited from the vote account, breaking lamport conservation and bank capitalization accounting. Also, note that inflation stake rewards (`stake_reward_lamports_minted`) are explicitly added to `capitalization` in `distribute_epoch_rewards_in_partition`, but `block_reward` amounts are not [6](#0-5) , confirming the design assumes block_reward lamports come from an existing, shrinking pool held in the vote account — which is exactly the invariant that appears to be unenforced.

### Likelihood Explanation
This path only fires when the `block_revenue_sharing` and SIMD-0123-related feature gates are active [7](#0-6) , and it executes deterministically for every validator during ordinary epoch-boundary reward calculation — no malicious peer, validator, or plugin assumption is required, satisfying the "unprivileged" and "non-malicious-actor" constraints.

### Recommendation
Trace and verify whether `pending_delegator_rewards` (or the vote account's lamport balance backing it) is decremented anywhere else in the codebase that my search did not surface (e.g., in code gated behind a feature I could not fully enumerate due to index size limits). If no such decrement exists, `calculate_block_reward`/`store_stake_accounts_in_partition` must subtract the distributed `block_reward` total from the vote account's `pending_delegator_rewards` field and debit the corresponding lamports from the vote account when stake accounts are credited, analogous to adding `reward[msg.sender] = 0` in the original bug.

### Proof of Concept
I was not able to construct a full end-to-end integration test within this investigation to conclusively prove double-payment across two consecutive epochs, since doing so requires running the full `apply_epoch_operations`/bank epoch-boundary test harness. I recommend a Devin session with terminal access run/extend the existing test `test_repeated_inflation_rewards_collector`-style harness in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` [8](#0-7)  across two epochs with `block_revenue_sharing` enabled and a non-zero `pending_delegator_rewards`, asserting whether `pending_delegator_rewards` (and vote-account lamports) change after the first epoch's distribution and whether stakers receive `block_reward` again in the second epoch from the same deposit.

**Caveat / uncertainty:** Due to codebase index size limits, I could not exhaustively confirm the absence of a decrement path across the full repository (e.g., in stake-program or a separate commission/collector settlement path not surfaced by search). This should be verified directly by reading the full `runtime/src/bank/partitioned_epoch_rewards/` module and `programs/vote/src/vote_state/` in a Devin session with full file access before treating this as a confirmed vulnerability.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
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
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L800-833)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L3893-3972)
```rust
    #[test]
    fn test_repeated_inflation_rewards_collector() {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.rent = Rent::default();
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();

        let collector_address = Pubkey::new_unique();
        let vote1_address = Pubkey::new_unique();
        let vote2_address = Pubkey::new_unique();
        // Vote account just created
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![
                    (
                        vote1_address,
                        VoteOperations {
                            create_with_balance: Some(LAMPORTS_PER_SOL),
                            new_commission: Some(50),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            new_inflation_rewards_collector: Some(collector_address),
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        vote2_address,
                        VoteOperations {
                            create_with_balance: Some(LAMPORTS_PER_SOL),
                            new_commission: Some(100),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            new_inflation_rewards_collector: Some(collector_address),
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );

        // next epoch, get double reward into collector
        let epoch = bank.epoch();
        apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch,
                vote_operations: vec![
                    (
                        vote1_address,
                        VoteOperations {
                            earned_credits: Some(1),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        vote2_address,
                        VoteOperations {
                            earned_credits: Some(1),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-204)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
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
