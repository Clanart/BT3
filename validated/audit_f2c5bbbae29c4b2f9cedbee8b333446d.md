[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/aptos-vm/src/transaction_metadata.rs (L303-309)
```rust
    pub fn entry_function_payload(&self) -> Option<EntryFunction> {
        self.entry_function_payload.clone()
    }

    pub fn multisig_payload(&self) -> Option<Multisig> {
        self.multisig_payload.clone()
    }
```

**File:** aptos-move/aptos-vm/src/transaction_metadata.rs (L311-326)
```rust
    pub fn as_user_transaction_context(&self) -> UserTransactionContext {
        UserTransactionContext::new(
            self.sender,
            self.secondary_signers.clone(),
            self.fee_payer.unwrap_or(self.sender),
            self.max_gas_amount.into(),
            self.gas_unit_price.into(),
            self.chain_id.id(),
            self.entry_function_payload()
                .map(|entry_func| entry_func.as_entry_function_payload()),
            self.multisig_payload()
                .map(|multisig| multisig.as_multisig_payload()),
            self.transaction_index_kind,
            self.is_encrypted_txn,
        )
    }
```

**File:** types/src/transaction/multisig.rs (L12-17)
```rust
pub struct Multisig {
    pub multisig_address: AccountAddress,

    // Transaction payload is optional if already stored on chain.
    pub transaction_payload: Option<MultisigTransactionPayload>,
}
```

**File:** types/src/transaction/multisig.rs (L41-63)
```rust
    pub fn as_transaction_executable(&self) -> TransactionExecutable {
        match &self.transaction_payload {
            Some(MultisigTransactionPayload::EntryFunction(entry)) => {
                TransactionExecutable::EntryFunction(entry.clone())
            },
            Some(MultisigTransactionPayload::Script(script)) => {
                TransactionExecutable::Script(script.clone())
            },
            None => TransactionExecutable::Empty,
        }
    }

    pub fn as_transaction_executable_ref(&self) -> TransactionExecutableRef<'_> {
        match &self.transaction_payload {
            Some(MultisigTransactionPayload::EntryFunction(entry)) => {
                TransactionExecutableRef::EntryFunction(entry)
            },
            Some(MultisigTransactionPayload::Script(script)) => {
                TransactionExecutableRef::Script(script)
            },
            None => TransactionExecutableRef::Empty,
        }
    }
```
