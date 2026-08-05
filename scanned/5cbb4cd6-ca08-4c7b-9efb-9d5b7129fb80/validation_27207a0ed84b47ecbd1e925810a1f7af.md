Given the extensive search, I was not able to conclusively verify that `pending_delegator_rewards` is ever decremented after block-reward distribution runs, despite checking every file that references the field. I present the strongest analog found, with that caveat noted explicitly.

### Title
Vote account `pending_delegator_rewards` is used as the basis for per-epoch block-reward distribution but is never decremented after payout, letting the same reserve be re-distributed every epoch - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`deposit_delegator_rewards` (SIMD-0123) transfers real lamports into a vote account and only ever *increases* `VoteStateV4::pending_delegator_rewards` via `add_pending_delegator_rewards` [1](#0-0) [2](#0-1) . Each epoch, `calculate_block_reward` reads that same cached field from a snapshotted vote-account view and computes each staker's proportional share of it [3](#0-2) . When rewards are actually paid out, `build_updated_stake_reward` credits stake accounts by minting new lamports (`stake_reward_lamports_minted`, added to `capitalization`) rather than withdrawing from the vote account's real balance or clearing its `pending_delegator_rewards` field [4](#0-3) [5](#0-4) . I could not locate any code path (in `distribution.rs`, `calculation.rs`, or the vote program) that reduces `pending_delegator_rewards` after this payout — the grep for `pending_delegator_rewards` across the whole repo only turned up the deposit path, the read-only usage in `calculate_block_reward`, the withdraw floor check, and view/decoder plumbing.

### Finding Description
This mirrors the Curve report's bug class: a *cached accounting value* (`admin_balances` in Curve; `pending_delegator_rewards` here) is treated as ground truth for a payout calculation, but nothing keeps it synchronized with what has actually been paid out. `withdraw()` in the vote program treats `pending_delegator_rewards()` as a live reserve that must remain in the account and blocks withdrawal/closure below that amount [6](#0-5) , which is consistent with the field being intended to represent "still-owed-to-delegators" funds. Yet the consumer of that same field, `calculate_block_reward`, uses it every epoch as the numerator for splitting rewards among all currently active stake accounts [7](#0-6) , and the corresponding payout mints brand-new lamports into stake accounts instead of transferring/burning from the vote account's balance or ever reducing the cached field [4](#0-3) .

### Impact Explanation
If `pending_delegator_rewards` is genuinely never decremented on the bank side, then every subsequent epoch's `calculate_block_reward` will again use the full (undiminished, and ever-growing via further deposits) balance as the reward pool, so the same lamports get "distributed" — via unconditional minting — repeatedly. This breaks the fixed/expected SOL supply invariant enforced elsewhere (e.g. `capitalization` tracking in `store_account_and_update_capitalization` [8](#0-7) ) by inflating capitalization without any matching burn or real transfer out of the vote account, analogous to LPs being diluted by admin fees calculated against a stale balance that never reflects the real, already-distributed value.

### Likelihood Explanation
This path is on the SIMD-0123/SIMD-0232/block-revenue-sharing feature path (`block_revenue_sharing`, `custom_commission_collector`), which is gated behind feature flags and only triggers once those features are activated network-wide, and only for vote accounts that actively receive delegator-reward deposits. It is not attacker-controlled in the sense of a malicious validator/peer; it is a systemic accounting question in a code path exercised automatically once per epoch by the runtime itself.

### Recommendation
Verify explicitly whether `pending_delegator_rewards` is meant to be reduced by `block_reward_lamports_distributed` after `store_stake_accounts_in_partition`/`distribute_epoch_rewards_in_partition` runs (i.e., a CPI-less in-place field update rather than mint), and if it is not currently reduced anywhere, add that decrement (and a comment on `calculate_block_reward` documenting the intended lifecycle of the field) so the reserved amount and the minted-reward accounting stay consistent, the same way the Curve report recommended documenting/reconciling `admin_balances` against real token movements.

### Proof of Concept
Not constructed — I was unable to trace, within the available tools, an end-to-end epoch-boundary test that shows `pending_delegator_rewards` before/after two consecutive `store_stake_accounts_in_partition` cycles to conclusively demonstrate double distribution. This should be validated by a Devin session with full repo/test access, running `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`'s `test_calculate_block_reward_*` tests alongside a two-epoch integration test that deposits once via `deposit_delegator_rewards`, runs `begin_partitioned_rewards`/distribution twice, and checks whether `pending_delegator_rewards` and `capitalization` behave as expected.

**Caveat:** Because I could not verify the absence of a decrement mechanism with full certainty (my search tools only index parts of the codebase and I exhausted my search iterations), this should be treated as a leads-based finding requiring confirmation with full source access, not a fully proven vulnerability.

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

**File:** programs/vote/src/vote_state/mod.rs (L1079-1122)
```rust
    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;

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
    }
```

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

**File:** runtime/src/bank.rs (L4819-4851)
```rust
    /// Technically this issues (or even burns!) new lamports,
    /// so be extra careful for its usage
    pub(crate) fn store_account_and_update_capitalization(
        &self,
        pubkey: &Pubkey,
        new_account: &AccountSharedData,
    ) {
        let old_account_data_size = if let Some(old_account) =
            self.get_account_with_fixed_root_no_cache(pubkey)
        {
            match new_account.lamports().cmp(&old_account.lamports()) {
                std::cmp::Ordering::Greater => {
                    let diff = new_account.lamports() - old_account.lamports();
                    trace!("store_account_and_update_capitalization: increased: {pubkey} {diff}");
                    self.capitalization.fetch_add(diff, Relaxed);
                }
                std::cmp::Ordering::Less => {
                    let diff = old_account.lamports() - new_account.lamports();
                    trace!("store_account_and_update_capitalization: decreased: {pubkey} {diff}");
                    self.capitalization.fetch_sub(diff, Relaxed);
                }
                std::cmp::Ordering::Equal => {}
            }
            old_account.data().len()
        } else {
            trace!(
                "store_account_and_update_capitalization: created: {pubkey} {}",
                new_account.lamports()
            );
            self.capitalization
                .fetch_add(new_account.lamports(), Relaxed);
            0
        };
```
