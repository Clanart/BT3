[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/src/account_saver.rs (L386-428)
```rust
    #[test]
    fn test_collect_accounts_for_failed_tx_rollback_fee_payer_only() {
        let from = keypair_from_seed(&[1; 32]).unwrap();
        let from_address = from.pubkey();
        let to_address = Pubkey::new_unique();
        let from_account_post = AccountSharedData::new(4199, 0, &Pubkey::default());
        let to_account = AccountSharedData::new(2, 0, &Pubkey::default());

        let instructions = vec![system_instruction::transfer(&from_address, &to_address, 42)];
        let message = Message::new(&instructions, Some(&from_address));
        let blockhash = Hash::new_unique();
        let transaction_accounts = vec![
            (message.account_keys[0], from_account_post),
            (message.account_keys[1], to_account),
        ];
        let tx = new_sanitized_tx(&[&from], message, blockhash);

        let from_account_pre = AccountSharedData::new(4242, 0, &Pubkey::default());

        let touched_flags =
            touched_flags_for_test(transaction_accounts.len(), transaction_accounts.len());
        let loaded = LoadedTransaction {
            accounts: transaction_accounts,
            // Worst case: every writable account appears modified, yet a failed
            // tx must still persist only its rollback accounts.
            touched_flags,
            fee_details: FeeDetails::default(),
            rollback_accounts: RollbackAccounts::FeePayerOnly {
                fee_payer: (from_address, from_account_pre.clone()),
            },
            compute_budget: SVMTransactionExecutionBudget::default(),
            loaded_accounts_data_size: 0,
        };

        let txs = vec![tx];
        let processing_results = vec![new_executed_processing_result(
            Err(TransactionError::InstructionError(
                1,
                InstructionError::InvalidArgument,
            )),
            loaded,
        )];
        let max_collected_accounts = max_number_of_accounts_to_collect(&txs, &processing_results);
```

**File:** program-runtime/src/cpi.rs (L839-843)
```rust
    // Process the callee instruction
    let mut compute_units_consumed = 0;
    invoke_context
        .process_instruction(&mut compute_units_consumed, &mut ExecuteTimings::default())?;

```

**File:** programs/vote/src/vote_state/mod.rs (L952-988)
```rust

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
