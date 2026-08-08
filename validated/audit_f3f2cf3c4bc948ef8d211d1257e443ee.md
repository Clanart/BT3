### Title
Unbounded memcpy of oversized nonce-account data into the fee payer in `RollbackAccounts::new` occurs before the `loaded_accounts_data_size_limit` is enforced - ([File: svm/src/rollback_accounts.rs])

### Summary
`RollbackAccounts::new` calls `fee_payer_account.set_data_from_slice(nonce.account().data())` in the `SameNonceAndFeePayer` branch while validating the fee payer, which happens *before* `load_transaction_accounts`/`LoadedTransactionDataSize` enforces the transaction's `loaded_accounts_data_size_limit`. Because nonce-account size is not strictly bounded to `NonceState::size()` unless `strict_nonce_size_check` is enabled, an attacker can construct an oversized, system-owned "nonce" account whose data is still parsed successfully by `verify_nonce_account`, forcing a full-size memcpy that is not accounted against the compute-budget/loaded-data-size limit the attacker set for the (deliberately failing) transaction.

### Finding Description
`RollbackAccounts::new` is invoked from `validate_transaction_fee_payer` [1](#0-0) , which runs inside `validate_transaction_nonce_and_fee_payer`, itself called prior to `load_transaction` / `load_transaction_accounts`, where the per-transaction `LoadedTransactionDataSize` (backed by `loaded_accounts_bytes_limit`) is actually enforced [2](#0-1) .

Inside `RollbackAccounts::new`, when the nonce account and fee payer share the same address, the code does:
```
fee_payer_account.set_data_from_slice(nonce.account().data());
``` [3](#0-2) 

The nonce account used here comes from `validate_transaction_nonce`, which loads the account and calls `verify_nonce_account` to check it parses as `State::Initialized` and matches the expected durable-nonce hash [4](#0-3) . Crucially, the only guard against an oversized nonce account is the *optional* `strict_nonce_size_check` flag, which compares `nonce_account.data().len() != NonceState::size()` [5](#0-4) . When this flag is not enabled, an account with data far larger than `NonceState::size()` still passes validation, as demonstrated by the existing unit test that explicitly builds a `NonceState::size() + 1`-byte account and shows the strict check is required to reject it [6](#0-5) . This is possible because `BorrowedInstructionAccount::set_state` only requires `serialized_size <= data.len()`, not equality, so the System Program's `InitializeNonceAccount`/`AdvanceNonceAccount` instructions happily operate on an over-allocated, system-owned account [7](#0-6) .

Exploit flow:
1. Attacker allocates (via System Program `Allocate`/`CreateAccount`) a system-owned account with a large data length (bounded only by the cluster's max account data size).
2. Attacker initializes it as a durable nonce (`InitializeNonceAccount`), which only writes the small `NonceState` struct into the front of the buffer, leaving the rest as arbitrary/zero bytes.
3. Attacker submits a transaction using this account as its own fee payer and durable-nonce account (`fee_paying_nonce` case, i.e., `SameNonceAndFeePayer`), with `ComputeBudgetInstruction::set_loaded_accounts_data_size_limit` set to a very small value, and an instruction guaranteed to fail (or simply relying on the loaded-data-size check to fail the tx later).
4. During validation, `validate_transaction_nonce` loads and re-serializes the nonce state into the (still large) buffer, and `validate_transaction_fee_payer` -> `RollbackAccounts::new` performs `set_data_from_slice` copying the entire oversized buffer into the fee payer's `AccountSharedData` — a memcpy proportional to the account's real (large) size.
5. Only afterward does `load_transaction_accounts` check `loaded_accounts_data_size_limit` and reject the transaction as `MaxLoadedAccountsDataSizeExceeded`, i.e. the transaction ends up as fee-only/failed, yet the large copy already occurred.

The `loaded_accounts_data_size_limit` compute-budget mechanism exists precisely to bound the leader's data-loading/copying work in proportion to what the fee payer is charged for; this code path lets the attacker pay for a tiny limit while forcing an unbounded-relative-to-limit copy.

### Impact Explanation
This falls under the "materially underpriced compute" bounty category: a transaction that is destined to fail against a self-selected, minimal `loaded_accounts_data_size_limit` can still force the validator to perform an expensive memcpy (bounded only by the cluster's maximum account size) during the rollback-account construction phase, ahead of the size-limit check that is supposed to bound per-transaction data-processing cost. Repeated across many packed transactions in a block, this allows an attacker to impose CPU cost on the leader disproportionate to the fee paid / compute units billed, since the rejected transaction is billed only the base transaction fee, not compute units for the copy.

### Likelihood Explanation
Exploitability depends on the account model: creating an over-sized, system-owned "nonce-like" account is achievable by an unprivileged user using only the System Program (`Allocate`, `InitializeNonceAccount`), no privileged access needed, and is repeatable per transaction. The main gating factor is whether `strict_nonce_size_check` is enabled cluster-wide (it appears to be a feature-gated remediation already present in the codebase specifically for this class of issue, as shown by the dedicated test `test_check_nonce_transaction_validity_strict_nonce_size_check_fail` and the `strict_nonce_size_check` parameter threaded through `check_age_and_compute_budget_limits`/`validate_transaction_nonce_and_fee_payer`). If that feature is active, oversized nonce accounts are already rejected in `validate_transaction_nonce`/`load_message_nonce_data` before reaching `RollbackAccounts::new`, closing this path. If it is not yet activated on a given cluster, the path described here is directly reachable.

### Recommendation
Enforce `strict_nonce_size_check` (or an equivalent fixed-size check `nonce_account.data().len() == NonceState::size()`) unconditionally before any nonce account is used to build `NonceInfo`/`RollbackAccounts`, rather than gating it behind a feature/flag that can be disabled or not-yet-activated. Alternatively, bound the `set_data_from_slice` copy in `RollbackAccounts::new`'s `SameNonceAndFeePayer` branch to `NonceState::size()` bytes (or reject non-conforming sizes outright) so the copy cost can never exceed the compute/data budget already validated for the account.

### Proof of Concept
Integration test plan (extending `svm/tests/integration_test.rs`'s nonce test helpers):
```rust
#[test]
fn oversized_nonce_fee_payer_bypasses_loaded_data_size_limit() {
    // 1. Create a fee-paying nonce account (fee_paying_nonce = true) whose data buffer
    //    is allocated much larger than NonceState::size(), e.g. 1 MiB, but only the
    //    front is initialized with a valid NonceState via System Program instructions
    //    (Allocate + Assign to system_program + InitializeNonceAccount).
    // 2. Build a transaction using this account as both fee payer and nonce account,
    //    with ComputeBudgetInstruction::set_loaded_accounts_data_size_limit(1) to force
    //    failure at the loaded-accounts-data-size check.
    // 3. Ensure `strict_nonce_size_check` is disabled/not yet active in the test bank
    //    configuration.
    // 4. Run the transaction through the SVM pipeline (e.g. via SvmTestEntry / bank
    //    processing) and assert:
    //    a. The transaction is rejected as MaxLoadedAccountsDataSizeExceeded (fees-only).
    //    b. Instrument/measure (or assert via a wrapped AccountSharedData that records
    //       memcpy length) that RollbackAccounts::new's set_data_from_slice call copied
    //       the full oversized buffer (~1 MiB), not bounded to the 1-byte limit or to
    //       NonceState::size().
}
```
Expected result demonstrating the bug: the copy performed inside `RollbackAccounts::new` (traceable via the size of `nonce.account().data()`) is on the order of the oversized account (megabytes), while the transaction's declared/paid `loaded_accounts_data_size_limit` is 1 byte — showing the copy work is unconstrained by the budget the attacker paid for.

### Citations

**File:** svm/src/transaction_processor.rs (L815-822)
```rust
        // Capture fee-subtracted fee payer account and next nonce account state
        // to commit if transaction execution fails.
        let rollback_accounts = RollbackAccounts::new(
            nonce_info,
            *fee_payer_address,
            loaded_fee_payer.account.clone(),
            fee_payer_loaded_rent_epoch,
        );
```

**File:** svm/src/transaction_processor.rs (L848-869)
```rust
        let Some(mut nonce_account) = account_loader
            .load_transaction_account(nonce_address, true)
            .map(|loaded| loaded.account)
        else {
            error_counters.account_not_found += 1;
            return Err(TransactionError::AccountNotFound);
        };

        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        }

        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };
```

**File:** svm/src/account_loader.rs (L433-445)
```rust
        TransactionValidationResult::Loadable(tx_details) => {
            let mut loaded_transaction_data_size =
                LoadedTransactionDataSize::with_max_size(tx_details.loaded_accounts_bytes_limit);

            let load_result = load_transaction_accounts(
                account_loader,
                message,
                tx_details.loaded_fee_payer_account,
                &mut loaded_transaction_data_size,
                error_metrics,
                rent,
            );

```

**File:** svm/src/rollback_accounts.rs (L71-87)
```rust
        if let Some(nonce) = nonce {
            if &fee_payer_address == nonce.address() {
                // `nonce` contains an AccountSharedData which has already been advanced to the current DurableNonce
                // `fee_payer_account` is an AccountSharedData as it currently exists on-chain
                // thus if the nonce account is being used as the fee payer, we need to update that data here
                // so we capture both the data change for the nonce and the lamports/rent epoch change for the fee payer
                fee_payer_account.set_data_from_slice(nonce.account().data());

                RollbackAccounts::SameNonceAndFeePayer {
                    nonce: (fee_payer_address, fee_payer_account),
                }
            } else {
                RollbackAccounts::SeparateNonceAndFeePayer {
                    nonce: (nonce.address, nonce.account),
                    fee_payer: (fee_payer_address, fee_payer_account),
                }
            }
```

**File:** runtime/src/bank/check_transactions.rs (L458-494)
```rust
    #[test]
    fn test_check_nonce_transaction_validity_strict_nonce_size_check_fail() {
        let (bank, _mint_keypair, custodian_keypair, nonce_keypair, _) =
            setup_nonce_with_bank(10_000_000, |_| {}, 5_000_000, 250_000, None).unwrap();
        let custodian_pubkey = custodian_keypair.pubkey();
        let nonce_pubkey = nonce_keypair.pubkey();

        let nonce_hash = get_nonce_blockhash(&bank, &nonce_pubkey).unwrap();
        let message = new_sanitized_message(Message::new_with_blockhash(
            &[
                system_instruction::advance_nonce_account(&nonce_pubkey, &nonce_pubkey),
                system_instruction::transfer(&custodian_pubkey, &nonce_pubkey, 100_000),
            ],
            Some(&custodian_pubkey),
            &nonce_hash,
        ));

        let nonce_account = bank.get_account(&nonce_pubkey).unwrap();
        let mut resized_nonce_account = AccountSharedData::new(
            nonce_account.lamports(),
            NonceState::size() + 1,
            nonce_account.owner(),
        );
        resized_nonce_account.data_as_mut_slice()[..nonce_account.data().len()]
            .copy_from_slice(nonce_account.data());
        bank.store_account(&nonce_pubkey, &resized_nonce_account);

        assert!(
            bank.check_nonce_transaction_validity(
                &message,
                &bank.next_durable_nonce(),
                true,
                false
            )
            .is_none()
        );
    }
```

**File:** transaction-context/src/instruction_accounts.rs (L251-268)
```rust
    /// Deserializes the account data into a state
    #[cfg(feature = "bincode")]
    pub fn get_state<T: serde::de::DeserializeOwned>(&self) -> Result<T, InstructionError> {
        bincode::deserialize(self.account.data()).map_err(|_| InstructionError::InvalidAccountData)
    }

    /// Serializes a state into the account data
    #[cfg(feature = "bincode")]
    pub fn set_state<T: serde::Serialize>(&mut self, state: &T) -> Result<(), InstructionError> {
        let data = self.get_data_mut()?;
        let serialized_size =
            bincode::serialized_size(state).map_err(|_| InstructionError::GenericError)?;
        if serialized_size > data.len() as u64 {
            return Err(InstructionError::AccountDataTooSmall);
        }
        bincode::serialize_into(&mut *data, state).map_err(|_| InstructionError::GenericError)?;
        Ok(())
    }
```
