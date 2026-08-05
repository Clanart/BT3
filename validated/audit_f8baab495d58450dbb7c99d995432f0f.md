[1](#0-0) [2](#0-1)

### Citations

**File:** svm/src/transaction_processor.rs (L790-800)
```rust
        let fee_payer_address = message.fee_payer();

        // We *must* use load_transaction_account() here because *this* is when the fee-payer
        // is loaded for the transaction. Transaction loading skips the first account and
        // loads (and thus inspects) all others normally.
        let Some(mut loaded_fee_payer) =
            account_loader.load_transaction_account(fee_payer_address, true)
        else {
            error_counters.account_not_found += 1;
            return Err(TransactionError::AccountNotFound);
        };
```

**File:** svm/src/transaction_processor.rs (L871-875)
```rust
        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);
```
