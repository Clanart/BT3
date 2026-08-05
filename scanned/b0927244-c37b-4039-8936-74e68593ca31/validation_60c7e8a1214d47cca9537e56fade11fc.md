[1](#0-0) [2](#0-1) [3](#0-2) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/src/bank/check_transactions.rs (L286-300)
```rust
    pub(super) fn load_message_nonce_data(
        &self,
        message: &impl SVMMessage,
        strict_nonce_size_check: bool,
    ) -> Option<(Pubkey, NonceData)> {
        let nonce_address = message.get_durable_nonce()?;
        let nonce_account = self.get_account_with_fixed_root(nonce_address)?;
        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            return None;
        }
        let nonce_data =
            nonce_account::verify_nonce_account(&nonce_account, message.recent_blockhash())?;

        Some((*nonce_address, nonce_data))
    }
```

**File:** svm/src/transaction_processor.rs (L861-869)
```rust
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

**File:** rpc-client-nonce-utils/src/nonblocking/mod.rs (L125-130)
```rust
pub fn state_from_account<T: ReadableAccount>(account: &T) -> Result<State, Error> {
    account_identity_ok(account)?;
    let versions: Versions =
        wincode::deserialize(account.data()).map_err(|_| Error::InvalidAccountData)?;
    Ok(State::from(versions))
}
```
