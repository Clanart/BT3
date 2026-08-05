[1](#0-0)

### Citations

**File:** poh/src/transaction_recorder.rs (L103-111)
```rust
    pub fn record(
        &self,
        bank_id: BankId,
        mixin: Hash,
        transactions: Vec<VersionedTransaction>,
    ) -> Result<Option<usize>, RecordSenderError> {
        self.record_sender
            .try_send(Record::new(mixin, transactions, bank_id))
    }
```
