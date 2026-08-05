### Title
Orphaned `pending_delegator_rewards` on a vote account with zero delegated stake are silently absorbed by the first staker who delegates afterward - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
This mirrors the sdeusd.move bug: a reward pool (`pending_delegator_rewards`) accumulates on a vote account while there are no eligible "holders" (stakers), and later the first staker to appear can claim a share disproportionate to the time they actually held stake, because the distribution formula does not track how long stake was absent versus present.

### Finding Description
Delegator (block) rewards are deposited into a vote account's `pending_delegator_rewards` field via `deposit_delegator_rewards`, which unconditionally adds to the balance regardless of whether the vote account currently has any delegated stake: [1](#0-0) 
The corresponding handler simply performs `checked_add` with no check on active stake: [2](#0-1) 

At distribution time, `calculate_block_reward` computes each stake account's share of `pending_delegator_rewards` proportional to `stake / total_active_stake` at the current reward epoch: [3](#0-2) 

If `total_active_stake == 0` at a given epoch, the function returns `0` for that epoch (line 211-212), meaning no lamports are distributed and — critically — nothing in this snippet decrements `pending_delegator_rewards` for the "unclaimed" epoch(s) it accrued while stakeless. The value simply persists on the vote account. When stake is later delegated (even a comparatively small amount, or delegated for only a fraction of an epoch), the very next reward calculation divides the *entire accumulated* `pending_delegator_rewards` value by the *current* `total_active_stake`, handing the newly-delegated stake account a share computed as if it had earned rewards during the empty period too.

This is structurally identical to the reported Move bug: `convert_to_shares()` used a naive `assets`-to-`shares` ratio ignoring unvested/undistributed amounts when `total_supply == 0`; here, `calculate_block_reward` uses a naive `stake / total_active_stake` ratio ignoring the fact that `pending_delegator_rewards` may have accrued entirely during a period when `total_active_stake` was 0, with no invariant preventing deposit-while-stakeless or requiring pro-rata attribution by time-of-accrual.

### Impact Explanation
A validator operator (or anyone able to trigger `deposit_delegator_rewards`, e.g., via block revenue sharing under SIMD-0123) who deposits rewards into a vote account with zero delegated stake, then arranges for a staker to briefly delegate to that vote account, allows that staker to capture rewards that were never actually earned by their capital — a fund-misattribution issue that skews `RewardInfo`/stake-reward payouts and effectively steals rewards intended to be forfeited/burned (or redistributed fairly) from the protocol's inflation reward accounting. Because it corrupts the exact lamport amount credited via `PartitionedStakeReward`/`stake_reward` in the epoch-rewards distribution path, it constitutes false/incorrect execution of reward accounting.

### Likelihood Explanation
This requires the specific combination of a vote account temporarily having zero delegated stake while `pending_delegator_rewards` is non-zero, followed by a stake delegation — a state achievable without any malicious/trusted-node assumption, purely through instruction sequencing (delegate/deactivate/deposit/re-delegate) available to any staker/validator identity pair. This is gated behind SIMD-0123 (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing` features) being active, so it is a real but feature-gated path rather than universally exploitable today.

### Recommendation
Track `pending_delegator_rewards` accrual against the stake-weighted time it was outstanding (e.g., snapshot/checkpoint the pool whenever `total_active_stake` transitions to/from zero, or forfeit/burn rewards accrued during zero-stake epochs instead of carrying them forward), analogous to preventing `transfer_in_rewards` from crediting a vault with no active shareholders.

### Proof of Concept
Not independently executable from static analysis alone; based on code-path tracing:
1. Vote account V delegates 0 stake (`total_active_stake == 0`).
2. Caller invokes `deposit_delegator_rewards` on V for amount `X` (allowed unconditionally per `add_pending_delegator_rewards`). [4](#0-3) 
3. One epoch (or more) passes with `total_active_stake == 0`; `calculate_block_reward` returns 0 each such epoch, and `pending_delegator_rewards` is not reduced by this code path.
4. A staker S delegates a small stake to V.
5. At the next reward epoch, `calculate_block_reward` computes `pending_delegator_rewards * stake(S) / total_active_stake` — with S being the sole/majority stake, S receives most or all of `X`, despite having delegated only after the rewards accrued. [5](#0-4) 

I was unable to fully confirm (index size limits prevented complete tracing) whether some other code path decrements `pending_delegator_rewards` proportionally or burns it when `total_active_stake == 0` at deposit vs. distribution time — a Devin session with full repo access should verify whether `distribution.rs` or `vote_processor.rs` contains any zero-stake forfeiture logic for `pending_delegator_rewards` before treating this as fully confirmed.

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
