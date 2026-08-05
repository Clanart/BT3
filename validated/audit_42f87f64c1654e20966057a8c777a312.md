## Analysis

The wxETH bug is a classic "reward pool accrues independent of the share-supply denominator" flaw: a value (`cashMinusLocked`) grows regardless of `totalSupply()`, and whoever is first to hold a non-zero share afterward can redeem the *entire* accumulated pool instead of only the rewards that accrued while they actually held a share.

Agave's SIMD-0123 block-revenue-sharing mechanism has the same structural shape.

### Title
First staker after a zero-active-stake period can claim the vote account's entire accumulated `pending_delegator_rewards` pool - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
A vote account's `pending_delegator_rewards` (the block-revenue pool to be shared with delegators, per SIMD-0123) accrues via `deposit_delegator_rewards`, completely independent of how much stake is currently delegated to that vote account. [1](#0-0)  When rewards are redeemed each epoch, `calculate_block_reward` pays out `pending_delegator_rewards * stake / total_active_stake` to each delegation, guarded only by `total_active_stake == 0 → 0`. [2](#0-1)  That guard only prevents a division-by-zero when nobody is staked to receive anything — it does not prevent the accumulated pool from surviving epochs where `total_active_stake == 0` and then being paid out **in full** to the first staker who appears afterward, exactly like wxETH's "virgin stake claims all drops."

### Finding Description
`add_pending_delegator_rewards` simply `checked_add`s any deposited amount to the vote account's `pending_delegator_rewards` field, with no relationship to the vote account's currently delegated stake: [3](#0-2) 

During epoch-reward calculation, `calculate_block_reward` computes each stake delegation's share of that pool using `total_active_stake` from `RewardEpochDelegatedStakes` for the rewarded epoch:
```rust
let total_active_stake = reward_epoch_delegated_stakes.delegated_stakes.get(&vote_pubkey)...
if total_active_stake == 0 {
    0
} else {
    ...
    (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
        .try_into().unwrap_or(u64::MAX).min(pending_delegator_rewards)
}
``` [4](#0-3) 

The `total_active_stake == 0` branch avoids a division-by-zero, but it does nothing to reset, discount, or discard `pending_delegator_rewards` that accrued while the vote account had zero (or near-zero) delegated stake. The pool simply keeps existing on the vote account, unclaimed, until *any* stake becomes active. At that point the new delegator's `stake / total_active_stake` ratio is computed purely from the *current* rewarded epoch's snapshot — with no accounting for how many prior epochs of deposits went into the pool while that delegator held no stake at all. If the new delegator is effectively the only one active in that rewarded epoch (`stake == total_active_stake`), the ratio is `1`, and the delegator claims the *entire* multi-epoch accumulated pool in a single epoch. This mirrors the wxETH root cause precisely: the accrual (`_accrueDrip()` / `deposit_delegator_rewards`) is independent of the denominator (`totalSupply()` / `total_active_stake`), so the guard against `denominator == 0` does not stop a later, disproportionately small stakeholder from capturing rewards that accrued before they ever held a position.

The code's own comment acknowledges the related edge case ("if stake account has already received rewards, it's possible to have `stake > total_active_stake`... this is harmless in practice, but we clamp it just to be safe") — showing the authors were aware `stake`/`total_active_stake` ratios here can exceed sane bounds, but the mitigation only clamps for overflow safety, not for fairness of first-claimant payout. [5](#0-4) 

### Impact Explanation
Any lamports deposited into a vote account's `pending_delegator_rewards` while that vote account has zero (or minimal) active stake are effectively "up for grabs" by whichever delegator's stake activates first afterward. This is a fund-theft-class issue in the runtime's reward distribution: a delegator who staked for zero or a tiny fraction of the accrual period can redeem rewards meant to be split proportionally across genuine stake-time, at the expense of subsequent delegators who would otherwise have shared in that pool, or at the expense of the fairness guarantees SIMD-0123 is meant to provide. Because this occurs inside `calculate_stake_rewards_and_commissions`/`calculate_block_reward`, which runs unconditionally as part of normal per-epoch reward redemption for every stake delegation, no privileged actor or malicious validator behavior is required — an ordinary, low-stake, unprivileged delegator can benefit merely by timing their delegation to a dormant vote account that has accumulated deposits.

### Likelihood Explanation
This requires: (1) a vote account experiencing full stake deactivation or having very low delegated stake for one or more epochs while `deposit_delegator_rewards` continues to be called for it (block-revenue sharing, per SIMD-0123, is expected to run continuously as blocks are produced), and (2) a delegator activating new stake to that vote account afterward. Both preconditions are realistic and unprivileged — no special access is needed, only ordinary staking/delegation actions and normal validator operation. I was not able to fully trace, within the available tool budget, whether `pending_delegator_rewards` is ever decremented on the vote account after a block reward is paid out (no such decrement call site was found in the reachable code), which would affect exact severity bounds (e.g., whether the pool is fully consumed after one claim or can be paid out again); this should be verified directly in the repository.

### Recommendation
Track delegation-weighted accrual rather than a flat lamport pool, e.g., record `pending_delegator_rewards` accrual against the *stake-epochs* it was earned over (similar to `credits_observed` for inflation rewards), or forbid/discount payout of rewards accrued during epochs when `total_active_stake == 0` for that vote account (e.g., route rewards accrued during zero-stake epochs to the validator itself or burn them, analogous to the wxETH fix of not accruing/distributing when the denominator is zero). At minimum, ensure the fraction paid to any delegation is bounded by the delegation's proportion of stake-time over the period the `pending_delegator_rewards` was accumulated, not just the instantaneous `total_active_stake` at redemption time.

### Proof of Concept
1. Vote account `V` has `total_active_stake == 0` for several epochs (e.g., all prior delegators unstaked).
2. During these epochs, block-revenue sharing continues to call `deposit_delegator_rewards`, accumulating `pending_delegator_rewards = X` lamports on `V` via `add_pending_delegator_rewards`. [6](#0-5) 
3. An unprivileged staker delegates a minimal stake `S` to `V`. Once `S` activates and becomes the sole entry in `RewardEpochDelegatedStakes.delegated_stakes` for `V` (`total_active_stake == S`), the reward-calculation for that rewarded epoch computes:
   `block_reward = pending_delegator_rewards * S / S = X` — i.e., the staker with only one epoch's worth of stake receives the entire multi-epoch pool `X`. [7](#0-6) 
4. This is directly exercised by the existing unit-test harness `get_block_reward_for_test`, which shows `calculate_block_reward` returning `pending_delegator_rewards` in full whenever `individual_stake == total_stake` (e.g. `get_block_reward_for_test(1, 1, 1, 0) == 1`), regardless of how many prior epochs contributed to that `pending_delegator_rewards` value. [8](#0-7)

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-231)
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
