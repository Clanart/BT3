## Analysis

The Lido report describes an *unprivileged accounting write* (`Burner` shares-burn) to a component (`stETH`) that a *separate* contract (`WstETH`) relies on for an invariant, causing a subsequent, otherwise-legitimate operation (`unwrap`) to become blocked/reverted — i.e., griefing of a normal-user function via manipulation of shared internal accounting state, with no check preventing the write from targeting that particular consumer.

The closest real Agave analog is the `VoteInstruction::DepositDelegatorRewards` instruction (SIMD-0123) in the vote program, which lets **any signer with a system-owned, rent-exempt-after-transfer account** deposit an arbitrary amount into *any* validator's vote account and unconditionally bump that vote account's `pending_delegator_rewards` counter — a value that the vote program's `withdraw` path then treats as a mandatory reserve that the authorized withdrawer cannot touch or bypass.

### Title
Permissionless `DepositDelegatorRewards` lets any account inflate `pending_delegator_rewards` and block a vote account's authorized withdrawer from closing/withdrawing funds - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
`deposit_delegator_rewards` only requires that the `source_address` account sign the transfer; it does not verify any relationship between the source and the vote account, its authorized voter, or its authorized withdrawer. [1](#0-0) 
Any caller can therefore CPI a System transfer into an arbitrary vote account and call `add_pending_delegator_rewards`, permanently increasing `pending_delegator_rewards` on that account for as little as a few lamports. [2](#0-1) 

### Finding Description
The vote program's `withdraw` function treats `pending_delegator_rewards` as an inviolable reserve: when it is non-zero, the account can never be fully closed, and partial withdrawals are capped at `lamports - rent_exempt_minimum - pending_delegator_rewards`. [3](#0-2) 
This is exactly the same broken-invariant shape as the Lido report: a value maintained by one code path (`Burner`/`DepositDelegatorRewards`) is silently consumed as a hard precondition by an unrelated, legitimate user operation (`unwrap`/`withdraw`), and nothing gates *who* is allowed to write that shared value or *how much* they may write into someone else's account.

`DepositDelegatorRewards` performs no authorization check tying the `source_address` to the vote account: it is only required to be a system-program-owned, signing account with sufficient lamports to cover the transfer. [4](#0-3) 
The instruction is fully permissionless with respect to the vote account being targeted — the vote account itself does not need to sign, and no field on the vote account (e.g. `authorized_withdrawer`, `node_pubkey`) is checked against the caller. [5](#0-4) 

Within the local index, I was unable to find any instruction or code path that *decreases* `pending_delegator_rewards` back toward zero (no "claim delegator reward" instruction is defined; searches for `sub_pending_delegator_rewards`/`ClaimDelegatorReward` returned no processing logic, only the single `add_pending_delegator_rewards` setter referenced in `handler.rs`). Because I could not confirm the existence of a redemption/decrement path from the indexed code, it is uncertain whether `pending_delegator_rewards` is ever reduced other than by being reserved during `withdraw`. If no such reduction path exists yet in this codebase state, every `DepositDelegatorRewards` deposit — no matter how small — permanently increases the amount the vote account's own authorized withdrawer can never withdraw or use to close the account, which is the direct analog of "Burner cannot burn shares in a way that later blocks `unwrap`."

### Impact Explanation
An attacker (any funded keypair, no special privileges) can send a `DepositDelegatorRewards` instruction against a target validator's vote account with a minimal deposit. This permanently raises `pending_delegator_rewards`, and by `withdraw`'s logic that amount becomes locked/unspendable by the account's rightful `authorized_withdrawer` — it can never be withdrawn and, if driven above the account's total balance minus rent-exempt minimum, can effectively make it impossible to fully close the vote account (`remaining_balance == 0` branch requires `pending_delegator_rewards == 0`). [6](#0-5) 
This is a griefing/fund-lockup vector against validator operators' vote accounts — an unprivileged actor can degrade or block a legitimate account-management operation (closing/fully withdrawing a vote account) without the target's consent, matching "cause fund theft/loss" and "false execution/acceptance"-adjacent impact through account-state corruption of a value load-bearing for a user-facing operation.

### Likelihood Explanation
Likelihood is high for the write primitive itself: `DepositDelegatorRewards` requires only a signer with a small amount of lamports and is not restricted to any authorized party, and every added test in `vote_processor.rs` confirms the instruction succeeds for a completely unrelated third-party `source_pubkey`. [7](#0-6) [8](#0-7) 
The severity of the resulting lockup depends on whether a decrement/claim mechanism exists elsewhere that I could not locate in the indexed code, so this should be verified against the full source before treating it as a confirmed permanent-fund-lock bug versus a temporary griefing annoyance.

### Recommendation
Restrict `DepositDelegatorRewards` so that only an authorized party (e.g., a specific delegator-rewards system account/PDA recognized by the protocol, or a check tying the deposit to the vote account's actual stake delegators via SIMD-0123's intended design) can increase `pending_delegator_rewards`, and/or confirm and, if missing, implement a corresponding decrement/claim path so `pending_delegator_rewards` cannot be inflated by unrelated parties and left stuck against the withdrawer indefinitely. At minimum, add an explicit invariant check that prevents `pending_delegator_rewards` from exceeding the vote account's spendable balance in a way that can permanently block account closure by its `authorized_withdrawer`.

### Proof of Concept
1. Fund a throwaway keypair `attacker` with a small amount of SOL (system-owned account).
2. Submit a transaction invoking `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` with `vote_account_index = 0` set to the victim validator's vote account (not a signer) and `sender_account_index = 1` set to `attacker` (signer), per the instruction's account layout. [9](#0-8) 
3. Observe `pending_delegator_rewards` on the victim vote account increase by `1`, with no participation or consent from the vote account's `authorized_withdrawer`. [10](#0-9) 
4. Repeat/scale the deposit so that `pending_delegator_rewards` approaches or exceeds the account's withdrawable balance; subsequent `Withdraw` calls by the legitimate `authorized_withdrawer` for the full balance now fail with `InstructionError::InsufficientFunds`, and full account closure is rejected outright while `pending_delegator_rewards > 0`. [6](#0-5) 

**Caveat:** I could not locate, within the indexed portion of this repository, any instruction that decreases `pending_delegator_rewards` (e.g. a delegator-claim instruction). This is necessary to fully assess whether the lockup is permanent or self-correcting over time; a Devin session with full repository access should verify this before treating the impact as confirmed fund lockup versus temporary griefing.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L936-951)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L974-988)
```rust
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

**File:** programs/vote/src/vote_processor.rs (L4849-4855)
```rust
        // Create source account with enough lamports to transfer.
        let source_pubkey = Pubkey::new_unique();
        let source_lamports = 1_000_000;
        let source_account =
            AccountSharedData::new(source_lamports, 0, &solana_sdk_ids::system_program::id());

        let deposit_amount = 100_000;
```

**File:** programs/vote/src/vote_processor.rs (L4862-4878)
```rust
        let instruction_accounts = vec![
            AccountMeta {
                pubkey: vote_pubkey,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: source_pubkey,
                is_signer: true,
                is_writable: true,
            },
            AccountMeta {
                pubkey: solana_sdk_ids::system_program::id(),
                is_signer: false,
                is_writable: false,
            },
        ];
```

**File:** programs/vote/src/vote_processor.rs (L5110-5140)
```rust
        // Success
        let resulting_accounts = process_instruction_with_cu_check(
            VoteProgramFeatures::all_enabled(),
            &instruction_data,
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Ok(()),
            DEPOSIT_DELEGATOR_REWARDS_COMPUTE_UNITS,
        );

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
