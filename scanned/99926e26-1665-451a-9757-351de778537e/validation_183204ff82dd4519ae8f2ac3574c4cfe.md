### Title
Delegator rewards (`pending_delegator_rewards`) become permanently locked in a vote account when `RewardEpochDelegatedStakes` has no (or a zero) entry for that vote account, with no rescue mechanism - (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
SIMD-0123 lets anyone deposit block-revenue-sharing rewards into a vote account's `pending_delegator_rewards` balance via `deposit_delegator_rewards`. These funds are only ever released back out during epoch-reward distribution, where `calculate_block_reward` divides `pending_delegator_rewards` among delegating stake accounts using `total_active_stake` as the denominator. If that denominator is `0` (the vote account is absent from, or has a zero entry in, `RewardEpochDelegatedStakes::delegated_stakes`), the function returns `0` for every delegation, and no lamports are ever subtracted from the vote account's `pending_delegator_rewards`. Because `withdraw()` refuses to release any lamports below `pending_delegator_rewards` and blocks full account closure while it is non-zero, and there is no rescue/reclaim instruction, the deposited rewards become permanently frozen in the vote account.

### Finding Description
`calculate_block_reward` computes the block reward for a stake delegation from the voter's `pending_delegator_rewards`, using the delegated-stake snapshot `RewardEpochDelegatedStakes`: [1](#0-0) 

If `total_active_stake` (looked up via `.unwrap_or(0)`) is `0`, the function short-circuits and returns `0` — no lamports are moved for that vote account this epoch: [2](#0-1) 

This denominator is populated per-epoch from `RewardEpochDelegatedStakes::set`, which is built from the **VAT-filtered** vote-account snapshot (`clone_and_filter_for_vat`), not the full unfiltered set: [3](#0-2) [4](#0-3) 

A vote account can be excluded from this filtered set (and thus have no/zero entry in `delegated_stakes`) for several unprivileged, non-malicious reasons: it lacks a BLS pubkey, its balance drops below `minimum_vote_account_balance_for_vat`, or it simply falls outside the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` by stake for that epoch. Test coverage even documents that filtering can leave `delegated_stakes` empty or missing an entry for a given vote account: [5](#0-4) [6](#0-5) 

Once `calculate_block_reward` returns `0`, the stake-reward distribution path only ever *adds* `block_reward` lamports to the **stake account**; it never touches the vote account's `pending_delegator_rewards` field: [7](#0-6) 

No code path anywhere in the runtime or vote program decrements `pending_delegator_rewards` outside of this same block-reward calculation (confirmed by exhaustive search — the only writers are `add_pending_delegator_rewards`, used by `deposit_delegator_rewards`): [8](#0-7) [9](#0-8) 

Meanwhile, `withdraw()` treats `pending_delegator_rewards` as a hard reserve: it blocks full account closure while it is non-zero, and caps any partial withdrawal at `lamports - pending_delegator_rewards - rent_exempt_minimum`: [10](#0-9) 

There is no instruction in `VoteInstruction` (searched `programs/vote/src/vote_processor.rs`) that allows the withdrawer, the depositor, or anyone else to rescue or force-redistribute a stuck `pending_delegator_rewards` balance once the epoch in which it should have been distributed passes with a zero-stake denominator for that vote account.

### Impact Explanation
This is a direct, permanent loss of funds: lamports deposited as block-revenue-sharing rewards for delegators can become irrecoverably locked in a vote account with no code path to release, redistribute, or rescue them, exactly mirroring the audit finding's "no rescue function, rewards permanently stuck" pattern. Because Alpenglow reward distribution is bounded and VAT-filters vote accounts every epoch based on stake/balance thresholds, any validator whose vote account temporarily drops out of the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` or briefly loses its minimum VAT balance in the same epoch it received a `DepositDelegatorRewards` deposit will have that deposit permanently frozen.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a vote account to have received a delegator-reward deposit and then, for the corresponding rewarded epoch, be excluded from (or have a zero entry in) `RewardEpochDelegatedStakes::delegated_stakes` — e.g., via VAT filtering (stake/balance drops, missing BLS key) or a transient zero-stake state. This is a normal, unprivileged operational condition (no malicious actor required), consistent with the original report's "possible zero total supply during a reward period" scenario, and can recur every epoch for any borderline validator.

### Recommendation
Add an explicit reconciliation/rescue mechanism for `pending_delegator_rewards`:
- When `calculate_block_reward`'s denominator is zero for a vote account in a given reward epoch, either roll the undistributed `pending_delegator_rewards` forward for redistribution in a subsequent epoch with a valid denominator, or emit a distinguishable event/marker so the authorized withdrawer can trigger a dedicated "reclaim undistributed delegator rewards" instruction.
- Alternatively, allow the withdrawer to withdraw `pending_delegator_rewards` directly (e.g., burn/pending amount reset with a corresponding lamport transfer) once it can be proven that no stake was eligible to claim it for one or more full epochs.

### Proof of Concept
1. Validator `V` calls `VoteInstruction::DepositDelegatorRewards { deposit: X }` on its vote account, incrementing `pending_delegator_rewards` by `X` lamports via `deposit_delegator_rewards`/`add_pending_delegator_rewards`. [11](#0-10) 
2. During epoch boundary processing for the rewarded epoch, `V`'s vote account fails VAT filtering that epoch (e.g., its stake momentarily drops, or it falls outside `MAX_ALPENGLOW_VOTE_ACCOUNTS`), so `filtered_distribution_vote_accounts` (and therefore `RewardEpochDelegatedStakes::delegated_stakes`) has no entry, or a zero entry, for `V`. [3](#0-2) 
3. For every stake delegated to `V`, `calculate_block_reward` looks up `total_active_stake` as `0` and returns `0`, so `block_reward = 0` for all of `V`'s delegators that epoch. [1](#0-0) 
4. `build_updated_stake_reward` adds `0` block reward to each stake account; `V`'s `pending_delegator_rewards` field is left unchanged at `X`. [7](#0-6) 
5. In all subsequent epochs, `withdraw()` continues to reserve `X` lamports as part of `pending_delegator_rewards`, refusing full closure and capping partial withdrawals — and there is no other instruction to reclaim it, so `X` lamports are permanently stuck. [10](#0-9)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L182-213)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2925-2936)
```rust
        let reward_epoch_delegated_stakes = RewardEpochDelegatedStakes::get(&bank)
            .expect("AG reward epoch delegated stakes must be persisted");
        assert_eq!(reward_epoch_delegated_stakes.epoch, bank.epoch() - 1);
        assert_eq!(
            reward_epoch_delegated_stakes.delegated_stakes.len(),
            crate::bank::MAX_ALPENGLOW_VOTE_ACCOUNTS
        );
        assert!(
            !reward_epoch_delegated_stakes
                .delegated_stakes
                .contains_key(&filtered_vote_pubkey)
        );
```

**File:** runtime/src/bank.rs (L1781-1790)
```rust
        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
```

**File:** vote/src/vote_account.rs (L212-245)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }

```

**File:** runtime/tests/vote_account.rs (L458-476)
```rust
#[test]
fn test_clone_and_filter_for_vat_empty_accounts() {
    let mut rng = rand::rng();
    let current_limit = 3000;
    let vote_accounts = new_staked_vote_accounts(
        &mut rng,
        current_limit,
        current_limit,
        Some(100), // Set all vote accounts to equal stake of 100.
        MIN_STAKE_FOR_STAKED_ACCOUNT,
        MAX_STAKE_FOR_STAKED_ACCOUNT,
        |_| 10_000_000_000,
    );
    // Since everyone has the same stake and the limit is 500 less than number of accounts,
    // all border stake peers are removed and we end up with no valid accounts.
    let filtered =
        vote_accounts.clone_and_filter_for_vat(current_limit - 500, MIN_STAKE_FOR_STAKED_ACCOUNT);
    assert_eq!(filtered.len(), 0);
}
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

**File:** programs/vote/src/vote_state/mod.rs (L1084-1121)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
```
