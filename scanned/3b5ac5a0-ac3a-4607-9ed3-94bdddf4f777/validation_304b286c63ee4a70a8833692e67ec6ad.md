[1](#0-0) [2](#0-1)

### Citations

**File:** poh/src/transaction_recorder.rs (L64-77)
```rust
            let (res, poh_record_us) = measure_us!(self.record(bank_id, hash, transactions));
            record_transactions_timings.poh_record_us = Saturating(poh_record_us);

            match res {
                Ok(starting_index) => {
                    starting_transaction_index = starting_index;
                }
                Err(RecordSenderError::InactiveBankId | RecordSenderError::Shutdown) => {
                    return RecordTransactionsSummary {
                        record_transactions_timings,
                        result: Err(PohRecorderError::MaxHeightReached),
                        starting_transaction_index: None,
                    };
                }
```

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
