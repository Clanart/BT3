## Analysis

The GMX report's broken invariant is: **an unprivileged party can inject a token/balance into a victim contract that the victim did not choose to accept, which then trips a precondition check (`balance == 0`) blocking a state transition the victim controls (migration).** The direct Agave analog is the `DepositDelegatorRewards` vote instruction (SIMD-0123), which lets *any signer* push lamports into *any* V4 vote account and permanently bump that vote account's `pending_delegator_rewards` field — a field that gates full closure of the vote account in `withdraw`.

### Title
Unauthorized `DepositDelegatorRewards` griefing blocks vote-account withdrawal/closure via forced `pending_delegator_rewards` inflation - (File: `programs/vote/src/vote_state/mod.rs`, `programs/vote/src/vote_processor.rs`)

### Summary
`VoteInstruction::DepositDelegatorRewards` is processed by `deposit_delegator_rewards()` and only requires that the *sender* account sign the lamport transfer — it never checks that the caller is the vote account's authorized withdrawer, node identity, or any other authority tied to the target vote account. [1](#0-0)  Any address can therefore push an arbitrary (even 1-lamport) "deposit" into a validator's vote account and increment its `pending_delegator_rewards` counter. [2](#0-1) 

That counter is later read by `withdraw()` as a hard gate: full account closure (`remaining_balance == 0`) is rejected outright while `pending_delegator_rewards > 0`, and partial withdrawals must additionally reserve `pending_delegator_rewards` lamports on top of rent-exemption. [3](#0-2) 

### Finding Description
This mirrors the GMX pattern exactly: the victim (vote account owner/withdrawer) intends to perform a state transition it fully controls — closing/withdrawing from its own vote account — but an unprivileged third party can inject a value into a field it doesn't own (`pending_delegator_rewards`) via a public entry point (`DepositDelegatorRewards`), which then trips a guard condition inside `withdraw()` that was designed to protect delegator funds, not to be attacker-controllable. Just as the GMX attacker deposited/received vester tokens and force-transferred them into `PirexGmx` to keep its `balanceOf` non-zero, here an attacker calls `DepositDelegatorRewards` with a nominal lamport amount to keep `pending_delegator_rewards` non-zero on a vote account it does not control. [1](#0-0) 

The guard exists to prevent draining a vote account before delegator rewards owed against `pending_delegator_rewards` are paid out [4](#0-3) , but there is no check anywhere in `deposit_delegator_rewards` verifying that the depositing party is authorized to add to this pool, nor any cap/authorization tying deposits to legitimate block-revenue distribution flows.

### Impact Explanation
The immediate effect is a griefing/DoS on vote-account lifecycle management: the authorized withdrawer cannot fully close/deinitialize the vote account (`VoteStateHandler::deinitialize_vote_account_state`) as long as an attacker keeps `pending_delegator_rewards > 0` [5](#0-4) , and partial withdrawals are additionally constrained to always leave `rent_exempt_minimum + pending_delegator_rewards` locked in the account [6](#0-5) . This blocks a legitimate validator operator's ability to decommission a vote account or fully recover its funds, an unprivileged-actor griefing vector against account state transitions — the same bug class judged Medium severity in the source report.

The pending amount is not permanently frozen — it is intended to be consumed by the normal epoch reward-distribution flow that reads `pending_delegator_rewards` in `calculate_block_reward` [7](#0-6)  — so the practical severity depends on how quickly/fully that consumption zeroes the field for accounts with low/no active delegated stake (`total_active_stake == 0` returns `0` reward, meaning `pending_delegator_rewards` for an idle/undelegated vote account would never get drained). I was unable to fully trace the exact decrement logic for `pending_delegator_rewards` beyond `calculate_block_reward`, so whether a griefed idle vote account can permanently retain a nonzero `pending_delegator_rewards` (making the block indefinite) versus only temporarily (until next reward distribution) is uncertain from the code reviewed.

### Likelihood Explanation
The path requires only a signed system transfer and knowledge of a target vote account's pubkey — no special privilege, no malicious validator/leader assumption, and it is reachable by any unprivileged transaction sender via a standard vote-program instruction, satisfying the "unprivileged" requirement.

### Recommendation
Restrict `DepositDelegatorRewards` to only be invocable via the runtime's block-revenue-sharing distribution path (native/CPI-gated), or require that the caller be an authorized party for the target vote account (e.g., its authorized withdrawer or a designated block-revenue collector), so that arbitrary third parties cannot inflate `pending_delegator_rewards` on accounts they do not control.

### Proof of Concept
1. Attacker identifies any initialized V4 vote account `V` (does not need to control it).
2. Attacker submits a transaction invoking `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with `V` as `vote_account_index` and their own funded keypair as `sender_account_index`, signing as sender. [8](#0-7) 
3. `deposit_delegator_rewards` only checks that the sender signed the transfer; it accepts any vote account as long as it deserializes to an initialized V4 state, and updates `pending_delegator_rewards` unconditionally. [9](#0-8) 
4. `V`'s `pending_delegator_rewards` is now `> 0`.
5. When `V`'s legitimate authorized withdrawer later attempts to fully withdraw/close the account via `withdraw()`, the `remaining_balance == 0` branch fails with `InstructionError::InsufficientFunds` because `pending_delegator_rewards > 0`, and any partial withdrawal must leave that amount locked. [10](#0-9)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L936-987)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1122)
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
