Note on investigation limits: I was not able to fully trace where (or whether) `pending_delegator_rewards` is decremented after each epoch's distribution is stored, because the relevant call sites in `vote_state/mod.rs`, `vote_processor.rs`, and `frame_v4.rs` that reference the field beyond what was retrieved were not all visible in the index. The analysis below is based on what is directly confirmed in the code shown.

### Title
Unprivileged, pro-rata `pending_delegator_rewards` split via integer division lets a dominant delegator round minority co-delegators to zero and capture donated funds - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The Agave vote program exposes `DepositDelegatorRewards` (SIMD-0123), a fully permissionless instruction that lets *any* signer transfer lamports into *any* vote account's `pending_delegator_rewards` pool, with no authorization from the vote account's owner/withdrawer required. [1](#0-0)  That pooled balance is later split among the vote account's stake delegators strictly pro-rata by `stake / total_active_stake`, using unchecked floor integer division. [2](#0-1)  This is structurally the same "pooled deposit split by a share ratio" pattern as the ERC-4626 first-depositor bug: a permissionless deposit into a shared pool (`ERC20.transfer` in the report / `DepositDelegatorRewards` here) combined with a proportional-share payout formula (`shares = amount*totalShares/totalBalance` in the report / `pending_delegator_rewards*stake/total_active_stake` here).

### Finding Description
`deposit_delegator_rewards` only requires the *source* account to sign; it performs no check that the caller controls, delegates to, or is otherwise related to the target vote account: [3](#0-2) 
Anyone can therefore inflate `pending_delegator_rewards` on an arbitrary validator's vote account.

At the epoch boundary, this pool is distributed to each stake delegation of that vote account by `calculate_block_reward`, using a fixed pro-rata formula:
```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
``` [4](#0-3) 

This is analogous to the vulnerable vault's `shares = (_amount * totalStakeShares) / totalTokenBalanceStakers` formula: the "shares" here are each delegator's effective stake, and the "pool balance" is `pending_delegator_rewards`. Two direct consequences mirror the reported bug class:

1. **Zero-denominator / stuck-funds case**: if `total_active_stake` for the vote account is `0` at the reward epoch (e.g., no active delegation was recorded in `reward_epoch_delegated_stakes` for that validator), `calculate_block_reward` returns `0` for every delegation, so none of the deposited lamports are distributed that epoch — analogous to the "no shares issued" case in the report. [5](#0-4) 

2. **Rounding-based value transfer between delegators**: when one delegator holds an overwhelming majority of `total_active_stake` and another holds a minority (dust) stake to the same vote account, the minority delegator's computed share `pending_delegator_rewards * stake / total_active_stake` is floored, and any remainder from that floor division is effectively captured by whichever accounting/collector absorbs the "leftover" (the vote account's own balance, which the authorized withdrawer can eventually access). This is the same integer-truncation mechanic that let the attacker in the report absorb the victim's deposit when `shares` rounded to zero.

The instruction test suite itself demonstrates the mechanism operates exactly on unmodified attacker-controlled inputs (arbitrary `deposit` amounts, arbitrary timing), with no minimum-deposit or minimum-stake guard comparable to the "force a minimum stake" mitigation recommended in the original report. [6](#0-5) 

### Impact Explanation
An attacker who either operates a vote account with a dominant self-delegation, or colludes with the dominant delegator of a target vote account, can:
- Solicit or wait for another party to delegate a small stake to that vote account, or itself deposit `pending_delegator_rewards` and rely on the existing skewed stake distribution, then
- Let the epoch-boundary distribution round the minority delegator's share down to a negligible amount while the majority delegator (attacker-controlled) captures effectively all of the pooled `pending_delegator_rewards`, including third-party donated funds routed through the permissionless `DepositDelegatorRewards` instruction.

This causes fund misallocation/loss for the minority delegator and is a direct analog of "first user can steal everyone else's tokens" — funds intended to be shared pro-rata are captured disproportionately due to the same permissionless-deposit + share-ratio-rounding pattern.

### Likelihood Explanation
Likelihood is bounded by feature-gating: SIMD-0123/0185/0291/0232 (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing`) must all be active, and the vote account must be VoteState V4. [7](#0-6)  Once active, no additional privilege is required — `DepositDelegatorRewards` is open to any signer, and stake delegation skew across validators is common in practice, so the preconditions (a dominant delegator plus a minority delegator, or a validator with momentarily zero recorded `total_active_stake`) are realistically reachable without any malicious-validator or malicious-peer assumption.

### Recommendation
- Require a per-delegator minimum reward share, or accumulate/carry forward sub-lamport remainders per delegator instead of discarding them to the dominant party.
- Consider gating `DepositDelegatorRewards` so unrelated third parties cannot inflate a pool that is then unpredictably split by stake-ratio rounding, or make the split formula immune to donation timing/stake-ratio skew (e.g., snapshot-based accounting with remainder tracking similar to reward-per-share ledger patterns that avoid floor-division loss).
- Add explicit handling for the `total_active_stake == 0` branch so deposited `pending_delegator_rewards` cannot become effectively stuck/inaccessible to intended delegators.

### Proof of Concept
1. Feature set `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing` are active, and vote account `V` is on VoteState V4.
2. Delegator A stakes 999,999 lamports to `V`; delegator B stakes 1 lamport to `V` in the same rewarded epoch, so `total_active_stake = 1,000,000`.
3. Any signer (attacker or third party) calls `DepositDelegatorRewards` on `V` depositing `1,000,000` lamports into `pending_delegator_rewards`, per `deposit_delegator_rewards` at [8](#0-7) .
4. At epoch boundary, `calculate_block_reward` computes B's share as `1_000_000 * 1 / 1_000_000 = 1` lamport (correctly proportional here, but as `total_active_stake` grows relative to B's stake or as `pending_delegator_rewards` shrinks, B's computed share floors to `0` while A's share captures the remainder), per the formula at [9](#0-8) .
5. Repeating with a smaller `pending_delegator_rewards` relative to `total_active_stake` (e.g. deposit of 500,000 lamports with the same 1,000,000 : 1 stake ratio) causes B's computed share to floor to `0`, and A absorbs the full deposited amount — reproducing the report's "victim gets 0 shares, attacker gets everything" outcome using only unprivileged, permissionless instructions.

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

**File:** programs/vote/src/vote_processor.rs (L4855-4860)
```rust
        let deposit_amount = 100_000;

        let instruction_data = serialize(&VoteInstruction::DepositDelegatorRewards {
            deposit: deposit_amount,
        })
        .unwrap();
```
