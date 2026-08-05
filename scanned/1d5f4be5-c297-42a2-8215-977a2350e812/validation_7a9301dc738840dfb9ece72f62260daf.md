## Title
Block-reward calculation uses a stale `pending_delegator_rewards` snapshot while the vote account's actual balance can change via permissionless deposits, causing under/over-payout of block rewards - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

## Summary
`calculate_block_reward` computes each stake account's share of a vote account's SIMD-0123 block reward using `pending_delegator_rewards` read from `distribution_epoch_vote_accounts`, a **vote-account snapshot taken up to a full epoch before distribution**. [1](#0-0)  That field, however, is not a fixed, privileged value: any signer can permissionlessly increase it at any time via `VoteInstruction::DepositDelegatorRewards`, which transfers lamports into the vote account and calls `add_pending_delegator_rewards`. [2](#0-1)  This is structurally the same broken invariant as the `AmpleEarn.setMerkleRoots` bug: an accounting value that off-chain/at-calculation-time is baked into a distribution calculation, while the live on-chain value can keep changing due to ordinary unprivileged user transactions occurring in the interim.

## Finding Description
The block-reward split for a validator's stakers is computed as:
```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into().unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```
using the `pending_delegator_rewards` value read from `distribution_epoch_vote_accounts`. [3](#0-2)  `CachedVoteAccounts::distribution_epoch_vote_accounts` is explicitly documented as "Vote account state from the beginning of the rewarded epoch. This snapshot state is saved a full epoch before being used to prevent last minute commission rugs." [4](#0-3) 

The problem is that `pending_delegator_rewards` is not analogous to commission (which is intentionally frozen to avoid last-minute rug-pulls) — it is a running balance of undistributed rewards owed to delegators, and it is mutated continuously by `deposit_delegator_rewards`, callable by anyone with lamports to transfer, with no admin/permission gating beyond the sender's own signature. [5](#0-4)  Between the epoch-boundary snapshot (`distribution_epoch_vote_accounts`) and the actual block-reward calculation/distribution (which can run up to a full epoch later, and is explicitly re-derived on `recalculate_stake_rewards` after snapshot restore), additional deposits can be made that are never reflected in the reward split calculation, because the calculation reads only the frozen snapshot value, not the current live vote-account state.

The withdraw path (`withdraw` in `programs/vote/src/vote_state/mod.rs`) separately enforces that lamports up to the *current* `pending_delegator_rewards` value cannot be withdrawn, using the live value from the account, not the epoch-start snapshot. [6](#0-5)  This creates a mismatch: the amount reserved against withdrawal (live, current `pending_delegator_rewards`) is decoupled from the amount actually distributed to delegators as block reward (calculated from a stale epoch-start snapshot). Deposits made after the snapshot but before/at distribution inflate the vote account's real `pending_delegator_rewards`/balance, but the reward-splitting math in `calculate_block_reward` never sees them, so the newly deposited lamports remain stuck as "pending" (still counted against withdrawal eligibility) but are never included in the proportional payout to stakers for that epoch — mirroring exactly how `accruedInterestInPayoutReserve` in the AmpleEarn report became decoupled from the merkle-root-encoded claim amounts due to intervening deposits/redeems.

Unlike the neighboring commission-distribution path, which the code explicitly defers to *after* calculation specifically to "reflect intervening account mutations" (see comment in `distribute_reward_commissions`: "This is intentionally deferred from calculation time so that any intervening account mutations ... are reflected"), [7](#0-6)  there is no equivalent re-sync mechanism for `pending_delegator_rewards` consumed in `calculate_block_reward` — it strictly uses the frozen snapshot.

## Impact Explanation
Impact is Medium: lamports deposited into a vote account as delegator rewards after the epoch-start snapshot are excluded from that epoch's block-reward distribution to delegators, effectively locking/misallocating those funds relative to their intended recipients (delegators lose expected reward share; the amount stays parked in the vote account counted against withdrawal but is not paid out proportionally). This is a fund-accounting bug reachable by an unprivileged user's own permissionless transaction (`DepositDelegatorRewards`), not by a malicious admin or trusted party.

## Likelihood Explanation
Likelihood is Medium: it requires the SIMD-0123 delegator-reward-deposit feature to be active and requires deposits to occur after the epoch-start `distribution_epoch_vote_accounts` snapshot but before/at the block-reward calculation for that epoch — a natural, easily reachable timing window since the snapshot is taken up to a full epoch in advance and deposits are unrestricted in timing.

## Recommendation
Re-derive the block-reward split from a value that reflects `pending_delegator_rewards` as of the actual reward-calculation/distribution time (or explicitly reconcile/carry-forward the delta between the snapshot value and the live value), analogous to how `distribute_reward_commissions` intentionally defers commission loading to reflect intervening mutations. Alternatively, decouple the withdrawal-protection reserve from the reward-distribution basis so they cannot silently diverge, and add an accounting check that all currently pending delegator-reward lamports are eventually accounted for by some epoch's distribution.

## Proof of Concept
Conceptual sequence (cannot be executed without a live devnet/test harness, but is fully derivable from the cited code):
1. At epoch `N` boundary, `distribution_epoch_vote_accounts` snapshots vote account `V`'s `pending_delegator_rewards = X`. [4](#0-3) 
2. Any user calls `VoteInstruction::DepositDelegatorRewards` on `V` with amount `Y`, permissionlessly increasing the live `pending_delegator_rewards` to `X + Y`. [2](#0-1) 
3. During block-reward calculation for epoch `N`'s distribution, `calculate_block_reward` reads the stale snapshot value `X` (not `X + Y`) via `distribution_epoch_vote_accounts.get(&vote_pubkey).pending_delegator_rewards()`. [8](#0-7) 
4. Delegators are paid their proportional share of only `X`, not `X + Y`; the `Y` lamports remain in the vote account, still blocked from full withdrawal by the *live* `pending_delegator_rewards` check in `withdraw`, [6](#0-5)  but never distributed to delegators in this epoch's payout — a permanent accounting mismatch between reserved and distributed lamports.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L183-231)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L369-373)
```rust
        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
```

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

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```
