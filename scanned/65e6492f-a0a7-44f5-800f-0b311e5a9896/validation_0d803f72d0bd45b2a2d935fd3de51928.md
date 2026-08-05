Based on my research, I found a strong Agave-native analog to the reported bug class: **a liability value that is tracked separately (like the report's `pendingPayouts`) but is not correctly reconciled against the balance/capitalization accounting when it is paid out**, in the SIMD-0123 block-revenue-sharing reward path (`pending_delegator_rewards`).

### Title
Block-reward distribution mints stake-account lamports from `pending_delegator_rewards` without debiting the vote account or matching capitalization/sysvar accounting - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs] / [File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs] / [File: runtime/src/bank/partitioned_epoch_rewards/sysvar.rs])

### Summary
`VoteStateV4::pending_delegator_rewards` is exactly the "pending payout" liability of the external report: lamports that have already been deposited into a vote account (via `deposit_delegator_rewards`) and are earmarked for delegators, tracked in a separate field [1](#0-0) . The withdrawal path correctly treats this as a liability and excludes it from the withdrawable vote-account balance [2](#0-1) . However, the epoch-reward "block reward" distribution path that pays this liability out to stakers computes each staker's share of `pending_delegator_rewards` [3](#0-2)  and mints it directly onto stake accounts via `checked_add_lamports` [4](#0-3) , while `distribute_epoch_rewards_in_partition` only adds `stake_reward_lamports_minted` (the inflation-rewards component) to `self.capitalization` — never `block_reward_lamports_distributed` [5](#0-4) . I could not find any code path in `calculation.rs`/`distribution.rs` that decrements the vote account's actual lamports or its `pending_delegator_rewards` field to correspond to the amount distributed as `block_reward`.

### Finding Description
The intended flow (per SIMD-0123) is: a vote account's `pending_delegator_rewards` represents lamports already sitting in the vote account, and the periodic "block reward" distribution should move those lamports from the vote account to the delegators' stake accounts, keeping total lamports (and thus capitalization) constant.

Instead:
1. `calculate_block_reward` reads `pending_delegator_rewards` from the vote account and computes each delegator's proportional share [6](#0-5) .
2. At distribution time, `build_updated_stake_reward` credits this `block_reward` straight onto the stake account with `checked_add_lamports`, with no corresponding read or debit of the *vote account itself* [4](#0-3) .
3. `distribute_epoch_rewards_in_partition` explicitly separates the two lamport categories: it adds only `stake_reward_lamports_minted` to `self.capitalization`, and separately calls `update_epoch_rewards_sysvar` to debit `block_reward_lamports_distributed` from the *EpochRewards sysvar account* (not the vote account) [7](#0-6) .
4. `update_epoch_rewards_sysvar` decrements the sysvar's own lamports for the debited block-reward amount "since block reward lamports already existed" [8](#0-7) . But at the point of entry into partitioned rewards (`begin_partitioned_rewards`), the sysvar is (re)created with `block_rewards` hard-coded to `0` [9](#0-8) .

The consequence is a structural mismatch identical in shape to the report's bug: a value (`pending_delegator_rewards` / block reward) is computed and paid out based on a "pending" balance sitting in one place (the vote account), but the ledger that is actually debited to make the accounting balance (the EpochRewards sysvar, seeded with `0`) never held those lamports in the first place. Existing guards do not stop this because:
- The withdrawal-side guard on the vote account (`checked_sub_lamports` bound by `pending_delegator_rewards`) only prevents the *withdraw authority* from removing the reserved lamports; it never verifies that a *distribution* actually removed them from the vote account.
- `update_epoch_rewards_sysvar`'s `checked_sub_lamports(debit_block_reward_lamports)` `.expect(...)` assumes the sysvar was pre-funded with the same `block_rewards` figure that will later be debited, but the call site that starts the epoch-reward cycle for the very code path exercising `block_revenue_sharing` (`begin_partitioned_rewards`) passes a literal `0`, not the true expected block-reward total.
- The `assert!` in `distribute_reward_commissions` only bounds `distributed_lamports + ... + total_stake_rewards_lamports` against `point_value.rewards`; it does not include `block_reward` in this invariant at all [10](#0-9) .

### Impact Explanation
If this path is reachable while `block_revenue_sharing` is active, one of two failure modes results:
- **Fund duplication / capitalization desync**: stake accounts are credited real lamports (`checked_add_lamports`) that are never subtracted from any other account and never added to `self.capitalization`, silently inflating the true lamport supply relative to the tracked capitalization invariant that downstream tooling and inflation calculations rely on — the same "shares become worth more than backing" outcome described in the external report.
- **Validator panic / consensus halt**: if the sysvar's `block_rewards` bucket is genuinely `0` (as `begin_partitioned_rewards` sets it) while `block_reward_lamports_distributed` is nonzero, `update_epoch_rewards_sysvar`'s `checked_sub_lamports(...).expect(...)` will underflow and panic every validator executing the same deterministic epoch-boundary code — a full-network halt rather than an isolated crash.

Both outcomes are High impact: one is silent fund/accounting corruption, the other is a deterministic, network-wide panic during normal epoch-boundary processing (no malicious peer, RPC client, or privileged actor required — it is triggered by ordinary reward distribution once the relevant feature is active).

### Likelihood Explanation
I could not fully verify reachability within the available inspection budget: it is possible another call site (outside `calculation.rs`/`distribution.rs`, which I could not locate) supplies a correct nonzero `block_rewards` figure to `create_epoch_rewards_sysvar` specifically for the Alpenglow/`block_revenue_sharing` path, which would close the sysvar-underflow scenario, or that vote-account debiting happens through a mechanism I did not find (e.g., inside `load_and_reward_commission_accounts`, which I did not inspect in depth). Given the codebase's own SIMD-0392 comment elsewhere emphasizing the exact danger of "lamports... double-counted" [11](#0-10) , the surrounding block-reward code shows the identical class of bug without a matching explicit guard, but confirming exploitability requires tracing the full `block_revenue_sharing`-enabled reward-distribution call graph, including the Alpenglow-specific entry point that isn't `begin_partitioned_rewards`.

### Recommendation
- Ensure the `EpochRewards` sysvar is seeded with the true total expected block-reward amount (sum of all `calculate_block_reward` outputs) whenever `block_revenue_sharing` is active, not a hard-coded `0`.
- Debit the originating vote account's lamports (and decrement `pending_delegator_rewards`) atomically with crediting the corresponding stake account, so the sysvar/capitalization bookkeeping and the actual token movement can never diverge.
- Extend the `assert!` in `distribute_reward_commissions` (and any equivalent invariant) to include `block_reward` totals so any mismatch between computed and available block-reward lamports fails fast in a controlled way rather than via an unrelated `.expect()` panic on the sysvar account.

### Proof of Concept
Not independently reproduced. Conceptually: (1) enable `block_revenue_sharing`, `custom_commission_collector`, `commission_rate_in_basis_points`; (2) fund a vote account's `pending_delegator_rewards` via `DepositDelegatorRewards`; (3) advance to an epoch boundary where the reward-calculation path computes `calculate_block_reward > 0` for at least one delegator, while the sysvar-creation path used at that boundary is `begin_partitioned_rewards` (block_rewards hard-coded `0`); (4) observe either an `.expect()` panic in `update_epoch_rewards_sysvar` during `checked_sub_lamports`, or (if some other seeding path is used) a divergence between `bank.capitalization()` and the true sum of all account lamports after distribution. Full confirmation requires locating the exact Alpenglow call site that produces `block_rewards` for `create_epoch_rewards_sysvar`, which I was unable to find within this investigation's scope — a Devin session with full-repo access would be needed to trace this call graph exhaustively and produce a runnable test.

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

**File:** programs/vote/src/vote_state/mod.rs (L1112-1121)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L276-282)
```rust
        self.create_epoch_rewards_sysvar(
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            distribution_starting_block_height,
            num_partitions,
            point_value,
            0, // block_rewards
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L49-54)
```rust
/// Adjusts stake delegation based on Rent sysvar parameters.
///
/// As part of SIMD-0392, if Rent is ever increased, we need to make sure that
/// lamports are not double-counted for the rent-exempt minimum and the stake
/// delegation. This function adjusts the delegation in a Stake if needed, right
/// at distribution time.
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

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L92-106)
```rust
        // Debit the lamports separately without updating capitalization,
        // since block reward lamports already existed
        let mut account = self
            .get_account_with_fixed_root(&sysvar::epoch_rewards::id())
            .expect("created sysvar account exists");

        // SAFETY: programmer error if we debit too many block rewards
        account
            .checked_sub_lamports(debit_block_reward_lamports)
            .expect("epoch reward sysvar has enough lamports for distribution");
        assert!(
            account.lamports() >= self.get_minimum_balance_for_rent_exemption(account.data().len()),
            "Sysvar account must have enough for rent exemption after debiting block rewards"
        );
        self.store_account(&sysvar::epoch_rewards::id(), &account);
```
