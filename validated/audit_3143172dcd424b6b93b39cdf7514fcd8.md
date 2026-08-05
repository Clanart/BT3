### Title
Fee payers are unfairly charged (`FeesOnly`) for transaction failures caused by external state changes outside their control - ([File: svm/src/account_loader.rs])

### Summary
The Atlas report's core complaint is that a party (the solver) is charged a cost (gas refund) for an outcome it cannot control (bundler-determined inclusion timing/gas price). The structural analog in Agave is the `FeesOnly` transaction-processing path: a transaction's fee payer is unconditionally charged the full transaction fee whenever account *loading* fails, even when the load failure is caused by external state changes that happened between signing and inclusion — changes that are controlled by the leader (who decides when to include the transaction) and by other, unrelated transactions (who may resize/close/replace accounts the sender referenced), not by the fee payer.

### Finding Description
When a transaction passes fee-payer/nonce validation but fails during `load_transaction_accounts` (e.g. because a referenced program account was closed, its executable status changed, or the cumulative loaded-account-data size limit was exceeded), the SVM commits it as `ProcessedTransaction::FeesOnly` and charges the fee payer the full `fee_details.total_fee()`: [1](#0-0) 

This path is explicitly reached from the main processing loop, which comments that "Transactions that fail at account loading charge fees and roll nonces": [2](#0-1) 

Tests confirm concrete cases that hit this path: a transaction targeting a now-missing/invalid program is charged the full signature fee (`ProgramAccountNotFound`, fee_details `5000`) even though the fee payer had no way to know the program would become invalid before the leader got around to including the transaction: [3](#0-2) 

and a transaction that grows past `MaxLoadedAccountsDataSizeExceeded` (driven by the current on-chain size of accounts, which can change due to *other* transactions writing to those accounts between signing and inclusion) is likewise routed to the fee-charging `FeesOnly` path: [4](#0-3) 

By contrast, Agave's designers already recognized and specifically fixed this exact class of problem for blockhash/nonce timing: when a transaction's `recent_blockhash` expires because the leader delayed inclusion, the transaction is rejected with `BlockhashNotFound` and the fee payer is **explicitly not charged** — this is asserted directly in tests ("Check fee not charged", "Check fee was *not* charged"): [5](#0-4) [6](#0-5) 

This shows the codebase's own design intent: fee should not be charged for failures attributable to leader-controlled timing rather than the signer. However, that principle is not applied consistently to the account-loading failure path, where the fee is charged regardless of whether the underlying cause (program closure/upgrade by its owner, account growth by unrelated transactions, leader delay in including the transaction before referenced state changed) was within the fee payer's control.

### Impact Explanation
A fee payer/sender can lose funds (the transaction fee, including any prioritization fee) for a transaction that never executes and whose failure was caused entirely by third parties: another account owner closing or reallocating an account the sender's transaction reads/writes, a program being upgraded/undeployed, or simply the leader sitting on the transaction long enough for on-chain state to drift. This is a direct, unfair fund loss for an unprivileged, honest transaction sender — consistent with the "fund loss for reasons outside the affected party's control" bug class from the source report. Because `FeesOnly` commits are part of normal block processing (not requiring any malicious peer/validator assumption — ordinary leader scheduling delay and ordinary third-party account writes suffice), this fits the "fund theft/loss" impact category for unprivileged users interacting with `runtime`/`accounts`/`transactions` paths.

### Likelihood Explanation
This requires no malicious behavior — only ordinary network conditions: (1) a transaction referencing a program/account, (2) some delay before leader inclusion (routine under load), and (3) an unrelated transaction (or a program upgrade) that changes the referenced account's executable state or size in the interim. Given normal transaction propagation delay and program upgrade cadence on mainnet, this is a routine occurrence rather than an edge case, making the likelihood high relative to how narrow the Atlas analog conditions were.

### Recommendation
Apply the same principle already used for `BlockhashNotFound`/nonce-authority failures to the account-loading failure path: audit which `TransactionError`s that produce a `FeesOnly` result are attributable to conditions outside the fee payer's control (e.g., `ProgramAccountNotFound`, `InvalidProgramForExecution`, `MaxLoadedAccountsDataSizeExceeded` caused by third-party account growth) and route those to a no-fee / `NoOp`-style outcome (as SIMD-0290's `NoOp` path already does for invalid fee payers), rather than unconditionally charging via `FeesOnlyTransaction`.

### Proof of Concept
1. Sender signs a transaction `Tx` at slot `N` invoking program `P`, with a valid `recent_blockhash` and sufficient fee-payer balance.
2. Before the leader includes `Tx` (still within the blockhash's valid age window, so `check_transaction_age` passes), the owner of `P` closes or upgrades `P` such that it is no longer a valid executable, OR an unrelated transaction writes to one of `Tx`'s referenced accounts, growing total loaded-account data size past the transaction's configured `loaded_accounts_bytes` limit.
3. Leader includes `Tx`. `check_transaction_age`/fee-payer validation succeeds (`validate_transaction_nonce_and_fee_payer`), so the flow proceeds to `load_transaction`.
4. `load_transaction_accounts` fails with `TransactionError::ProgramAccountNotFound` / `InvalidProgramForExecution` / `MaxLoadedAccountsDataSizeExceeded` — see `svm/src/account_loader.rs:456-469`.
5. `load_and_execute_sanitized_transactions` treats this as `TransactionLoadResult::FeesOnly` and calls `account_loader.update_accounts_for_failed_tx`, deducting the full fee from the fee payer — `svm/src/transaction_processor.rs:519-529`.
6. Result: the fee payer's balance is reduced by `fee_details.total_fee()` (as shown concretely in the `test_load_and_execute_commit_transactions_fees_only` test, `runtime/src/bank/tests.rs:1921-1935`) despite having no control over the program closure/upgrade or the unrelated account growth that caused the failure — mirroring the Atlas pattern of an unprivileged party being charged for a cost driven by a third party's action/timing.

### Citations

**File:** svm/src/account_loader.rs (L456-469)
```rust
                Err(err) => TransactionLoadResult::FeesOnly(FeesOnlyTransaction {
                    load_error: err,
                    fee_details: tx_details.fee_details,
                    loaded_accounts_data_size: if account_loader
                        .feature_set
                        .define_ltds_fee_only_semantics
                    {
                        loaded_transaction_data_size.into()
                    } else {
                        tx_details.rollback_accounts.data_size() as u32
                    },
                    rollback_accounts: tx_details.rollback_accounts,
                }),
            }
```

**File:** svm/src/account_loader.rs (L2304-2361)
```rust
    #[test]
    fn test_load_accounts_v1_instructions_sysvar_overflow() {
        const NUM_INSTRUCTIONS: usize = 64;
        const ACCOUNTS_PER_INSTRUCTION: usize = 31;

        let fee_payer = Pubkey::new_unique();
        let instructions_sysvar = sysvar::instructions::id();
        let program = native_loader::id();
        let message = v1::Message::new(
            MessageHeader {
                num_required_signatures: 1,
                num_readonly_signed_accounts: 0,
                num_readonly_unsigned_accounts: 2,
            },
            v1::TransactionConfig::empty(),
            Hash::default(),
            vec![fee_payer, instructions_sysvar, program],
            vec![
                CompiledInstruction {
                    program_id_index: 2,
                    accounts: vec![1; ACCOUNTS_PER_INSTRUCTION],
                    data: vec![],
                };
                NUM_INSTRUCTIONS
            ],
        );
        message.validate().unwrap();
        assert!(
            1 + message.size() + Signature::default().as_ref().len() <= v1::MAX_TRANSACTION_SIZE
        );

        let sanitized_message =
            SanitizedMessage::V1(v1::CachedMessage::new(message, &HashSet::new()));
        let mock_bank = TestCallbacks::default();
        let mut account_loader = (&mock_bank).into();

        let fee_payer_account = AccountSharedData::new(200, 0, &Pubkey::default());
        let load_result = load_transaction(
            &mut account_loader,
            &sanitized_message,
            TransactionValidationResult::Loadable(ValidatedTransactionDetails {
                loaded_fee_payer_account: LoadedTransactionAccount {
                    account: fee_payer_account,
                    loaded_size: TRANSACTION_ACCOUNT_BASE_SIZE,
                },
                ..ValidatedTransactionDetails::default()
            }),
            &mut TransactionErrorMetrics::default(),
            &Rent::default(),
        );

        assert!(matches!(
            load_result,
            TransactionLoadResult::FeesOnly(FeesOnlyTransaction {
                load_error: TransactionError::MaxLoadedAccountsDataSizeExceeded,
                ..
            }),
        ));
```

**File:** svm/src/transaction_processor.rs (L519-529)
```rust
                // Loading failures that would be fee-only become errors with `drop_on_failure`
                TransactionLoadResult::FeesOnly(FeesOnlyTransaction { load_error: e, .. })
                    if config.drop_on_failure =>
                    Err(e),

                // Transactions that fail at account loading charge fees and roll nonces
                TransactionLoadResult::FeesOnly(fees_only_tx) => {
                    account_loader
                        .update_accounts_for_failed_tx(&fees_only_tx.rollback_accounts, self.slot);
                    Ok(ProcessedTransaction::FeesOnly(Box::new(fees_only_tx)))
                }
```

**File:** runtime/src/bank/tests.rs (L1885-1935)
```rust
    // Invoke missing program to trigger load error in order to commit a
    // fees-only transaction
    let missing_program_id = Pubkey::new_unique();
    let transaction = Transaction::new_unsigned(Message::new_with_blockhash(
        &[
            system_instruction::advance_nonce_account(&nonce_pubkey, &fee_payer),
            Instruction::new_with_bincode(missing_program_id, &0, vec![]),
        ],
        Some(&fee_payer),
        &nonce_data.blockhash(),
    ));

    let mut loaded_accounts_data_size = 0;
    if define_ltds_fee_only_semantics {
        for key in &transaction.message.account_keys {
            if let Some(n) = bank
                .get_account_shared_data(key)
                .map(|(account, _)| account.data().len())
            {
                loaded_accounts_data_size += (n + TRANSACTION_ACCOUNT_BASE_SIZE) as u32
            }
        }
    } else {
        loaded_accounts_data_size = nonce_size as u32;
    }

    let batch = bank.prepare_batch_for_tests(vec![transaction]);
    let commit_results = bank
        .load_execute_and_commit_transactions(
            &batch,
            ExecutionRecordingConfig::new_single_setting(true),
            &mut ExecuteTimings::default(),
            None,
        )
        .0;

    assert_eq!(
        commit_results,
        vec![Ok(CommittedTransaction {
            status: Err(TransactionError::ProgramAccountNotFound),
            log_messages: None,
            inner_instructions: None,
            return_data: None,
            executed_units: 0,
            fee_details: FeeDetails::new(5000, 0),
            loaded_account_stats: TransactionLoadedAccountsStats {
                loaded_accounts_count: 2,
                loaded_accounts_data_size,
            },
            fee_payer_post_balance: fee_payer_initial_balance - 5000,
        })]
```

**File:** runtime/src/bank/tests.rs (L4109-4120)
```rust
    /* Expect a non-Nonce transfer to fail */
    assert_eq!(
        bank.process_transaction(&system_transaction::transfer(
            &custodian_keypair,
            &alice_pubkey,
            100_000,
            nonce_hash
        ),),
        Err(TransactionError::BlockhashNotFound),
    );
    /* Check fee not charged */
    assert_eq!(bank.get_balance(&custodian_pubkey), 4_750_000);
```

**File:** runtime/src/bank/tests.rs (L4376-4390)
```rust
    assert_eq!(
        bank.process_transaction(&nonce_tx),
        Err(TransactionError::BlockhashNotFound),
    );
    /* Check fee was *not* charged and nonce has *not* advanced */
    let mut recent_message = nonce_tx.message;
    recent_message.recent_blockhash = bank.last_blockhash();
    assert_eq!(
        bank.get_balance(&custodian_pubkey),
        initial_custodian_balance
    );
    assert_eq!(
        nonce_hash,
        get_nonce_blockhash(&bank, &nonce_pubkey).unwrap()
    );
```
