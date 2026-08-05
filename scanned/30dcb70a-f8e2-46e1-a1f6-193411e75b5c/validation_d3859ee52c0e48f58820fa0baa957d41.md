## Title
Unfair/incorrect delegator reward attribution when `pending_delegator_rewards` accumulates while `total_active_stake` is zero - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The block-revenue-sharing reward path (SIMD-0123) accumulates delegator rewards in a vote account's global `pending_delegator_rewards` balance via `deposit_delegator_rewards`, independent of whether any stake is currently effectively active on that vote account. When `calculate_block_reward` later distributes this pool, it divides the *entire* accumulated `pending_delegator_rewards` value by the *current epoch's* `total_active_stake`, with no per-epoch checkpointing of how much of that pool accrued while stake was zero (or while different delegators were staked). This reproduces the exact bug class described in TOKE-5: reward accounting that "freezes" while the denominator (stake/supply) is zero, then pays out the entire frozen backlog to whichever staker happens to be active once the denominator becomes non-zero.

### Finding Description
`deposit_delegator_rewards` (SIMD-0123) lets a signer transfer lamports into a vote account and unconditionally increments `pending_delegator_rewards` via `add_pending_delegator_rewards`, with no requirement that the vote account currently have any active stake delegated to it: [1](#0-0) [2](#0-1) 

During epoch-reward calculation, `calculate_block_reward` computes each stake delegation's share of this pool as `pending_delegator_rewards * stake / total_active_stake`, and explicitly returns `0` (i.e., skips accounting) whenever `total_active_stake == 0` for that reward epoch: [3](#0-2) 

Crucially, when `total_active_stake == 0`, the function returns `0` and no lamports are debited from `pending_delegator_rewards` for that epoch — the pool simply carries forward unchanged into the next epoch, exactly analogous to `AbstractRewarder::_updateReward()` skipping `lastUpdateBlock` when `rewardPerTokenStored`/`totalSupply` is zero. There is no mechanism that tracks *which* deposits arrived during periods of zero (or low) active stake versus periods when specific delegators were staked; the whole accumulated balance is a single flat counter consumed proportionally to whoever's stake happens to be effectively active in the epoch being processed: [4](#0-3) 

Because `deposit_delegator_rewards` has no dependency on stake state, a block-revenue collector can (deliberately or incidentally) deposit into `pending_delegator_rewards` while `total_active_stake` for the vote account is zero — e.g., before any delegator's stake has completed warm-up, or after all delegators have deactivated. Once a new delegation becomes effectively active (`delegation_effective_stake` transitions from 0 to non-zero, which happens automatically after the warm-up period per `StakeHistory`), that delegator's share is computed against the *full* backlog of `pending_delegator_rewards`, not just the portion that accrued while they personally held active stake.

### Impact Explanation
This breaks the invariant that block-revenue rewards should be distributed to delegators in proportion to the stake-time they actually contributed while the funds were being earned/deposited. Instead:
- A new delegator who activates stake immediately after a stakeless (or low-active-stake) period receives a share of rewards that accrued before their stake was active, at the expense of other, more deserving delegators (either ones who left, or ones the validator intended to reward under different terms).
- Because commission/vote-account operators control the timing of `DepositDelegatorRewards` calls and the size of deposits, this can be leveraged to funnel disproportionate rewards to a specific delegator by timing deposits during a zero-stake window and then activating a large stake right after, at the expense of the broader reward pool's fairness — a fund-loss/misallocation condition for other stakers.

### Likelihood Explanation
Requires `block_revenue_sharing`, `commission_rate_in_basis_points`, and `custom_commission_collector` features enabled (SIMD-0123/0291/0232), and requires a period where `total_active_stake` for a given vote account is zero while `pending_delegator_rewards` is non-zero — a state reachable through ordinary un-privileged actions (any signer can call `DepositDelegatorRewards`; stake deactivation/warm-up timing is normal user behavior), so no malicious validator/peer assumption is needed.

### Recommendation
Track `pending_delegator_rewards` distribution on a per-epoch or per-deposit basis so that rewards deposited while `total_active_stake == 0` are either escrowed until fairly attributable, or redirected (e.g., to the collector/incinerator) rather than being silently paid out in full to whichever stake becomes active first, mirroring the TOKE-5 remediation of queuing/quarantining rewards accrued during zero-supply windows instead of paying them to the next depositor.

### Proof of Concept
1. Create a `VoteStateV4` account with `block_revenue_sharing` enabled and no delegated stake (`total_active_stake == 0`).
2. Repeatedly call `VoteInstruction::DepositDelegatorRewards` to grow `pending_delegator_rewards` (as exercised in `test_deposit_delegator_rewards`-style tests): [5](#0-4) 
3. During these epochs, `calculate_block_reward` returns `0` for all epochs because `total_active_stake == 0` (see branch at lines 211-212 of `calculation.rs`), so the pool is never debited.
4. A delegator now delegates and warms up their stake; once `delegation_effective_stake` for that delegation becomes non-zero, `total_active_stake` becomes non-zero for the reward epoch, and `calculate_block_reward` pays out `pending_delegator_rewards * stake/total_active_stake` — i.e., the delegator's current share of the *entire* historical backlog, exactly as tested in `test_calculate_block_reward_specific`'s "get everything" case: [6](#0-5) 

Note: I was not able to locate, within the indexed portion of the codebase, the exact code path that debits/zeroes `pending_delegator_rewards` on the vote account after a successful distribution (only the stake-account-side crediting in `distribution.rs` was found); confirming the precise decrement mechanics and whether any additional per-epoch checkpoint exists would require inspecting the full `distribution.rs` / `load_and_reward_commission_accounts` implementation directly, which may not be fully covered by this index — a Devin session with full repo access would be needed to verify that detail conclusively.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L935-988)
```rust
/// Deposit delegator rewards into a vote account (SIMD-0123).
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4320-4332)
```rust
    #[test]
    fn test_calculate_block_reward_specific() {
        // get nothing
        assert_eq!(get_block_reward_for_test(0, 0, 0, 0), 0);
        // get everything
        assert_eq!(get_block_reward_for_test(1, 1, 1, 0), 1);
        // individual stake higher than block reward, capped
        assert_eq!(get_block_reward_for_test(2, 1, 1, 0), 1);
        // not truncated
        assert_eq!(get_block_reward_for_test(1, 10, 10, 0), 1);
        // truncated
        assert_eq!(get_block_reward_for_test(1, 10, 9, 0), 0);
    }
```

**File:** programs/vote/src/vote_processor.rs (L5120-5140)
```rust
        // Vote account should have been credited `deposit_amount`.
        // Source account should have been debited `deposit_amount`.
        // Vote state's `pending_delegator_rewards` should be updated.
        let vote_account_starting_lamports = vote_account_v4.lamports();
        let source_account_starting_lamports = source_lamports;
        let resulting_vote_account = &resulting_accounts[0];
        let resulting_source_account = &resulting_accounts[1];
        let vote_state =
            deserialize_vote_state_for_test(resulting_vote_account.data(), &vote_pubkey);
        assert_eq!(
            resulting_vote_account.lamports(),
            vote_account_starting_lamports + deposit_amount,
        );
        assert_eq!(
            resulting_source_account.lamports(),
            source_account_starting_lamports - deposit_amount,
        );
        assert_eq!(
            vote_state.as_ref_v4().pending_delegator_rewards,
            deposit_amount,
        );
```
