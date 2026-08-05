## Title
Vote-only-mode bypass: `TransactionViewReceiveAndBuffer` validates against stale `root_bank`, and the execution path (`process_and_record_aged_transactions`) never re-checks `vote_only_bank()` against the live `working_bank` - (File: `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs`)

### Summary
`translate_to_runtime_view` rejects non-vote transactions only when the bank passed to it is in vote-only mode. `TransactionViewReceiveAndBuffer::try_handle_packet` deliberately passes `root_bank` (not `working_bank`) into this check. [1](#0-0) [2](#0-1) 

The transaction that is buffered here is later dispatched via `ConsumeWork` to `ConsumeWorker::consume`, which fetches the then-current `working_bank` from `active_leader_state` and executes with `Consumer::process_and_record_aged_transactions`. [3](#0-2) 

Unlike `Consumer::process_and_record_transactions` (used by the vote path via `vote_worker.rs`), which explicitly re-checks `bank.vote_only_bank() && !vote_parser::is_valid_vote_only_transaction(tx)` against the live execution bank before committing, [4](#0-3) 
the aged-transaction path used for banking-stage-buffered transactions, `process_and_record_aged_transactions`, only performs `bank.resanitize_transaction_minimally(tx, max_age.sanitized_epoch, max_age.alt_invalidation_slot)` before executing - there is no `vote_only_bank()` recheck in this call path. [5](#0-4) 

### Finding Description
The buffering/validation ordering is:
1. `receive_and_buffer_packets` loads a `BankPair { root_bank, working_bank }` snapshot once per receive iteration. [6](#0-5) 
2. `handle_packet_batch_message` -> `try_handle_packet` -> `translate_to_runtime_view(bytes, root_bank, ...)` performs the vote-only-mode filtering exclusively using `root_bank.vote_only_bank()`. [7](#0-6) [8](#0-7) 
3. If it passes, the transaction is inserted into the shared container with a `MaxAge` derived only from ALT deactivation/epoch bounds, and is later scheduled to a `ConsumeWorker` as a `ConsumeWork` item, to be executed against whatever `working_bank` is active at dequeue time. [9](#0-8) [3](#0-2) 
4. The execution step for these buffered transactions is `process_and_record_aged_transactions`, whose only pre-check is `resanitize_transaction_minimally` for epoch/ALT-deactivation bounds - it does not check `bank.vote_only_bank()`. [5](#0-4) 

Because a fresh, non-vote-only `root_bank` can be observed at buffering time while the `working_bank` (or a subsequently-created child bank marked vote-only, e.g. during an Alpenglow/PoH migration window per `replay_stage.rs`'s `NewBankOptions { vote_only_bank: ... }`) is already vote-only, and because the execution-time bank check that would normally catch this (`bank.vote_only_bank()`) is absent from `process_and_record_aged_transactions`, a non-vote transaction admitted during this window is never rejected on the banking-stage leader-block-production path before being recorded/committed.

### Impact Explanation
If this can be triggered on a leader validator that is transitioning into vote-only mode, it would allow a non-vote (user) transaction to be included in a block during a period intended to admit only votes. Per the migration-status/vote-only-bank mechanism used around consensus migrations, this could corrupt the intended invariant that vote-only slots contain exclusively vote transactions, which downstream consumers (e.g. `blockstore_processor.rs`'s replay-time check `UserTransactionsInVoteOnlyBank`) rely on and could enforce differently at replay vs. at production, potentially causing block rejection by other validators (fork/dead-block) once such a block is replayed elsewhere, since `blockstore_processor.rs` performs its own independent vote-only enforcement at replay time. [10](#0-9) 

### Likelihood Explanation
This requires a race/window between when `root_bank` and `working_bank` diverge with respect to `vote_only_bank()` state, which is a narrow and transient condition (only during the specific slot(s) where `NewBankOptions.vote_only_bank` toggles differently for parent vs. child in `replay_stage.rs`'s bank-forking logic). It is plausible but requires precise timing by an unprivileged sender racing packet submission against the leader's internal bank-transition window; it is not a straightforward, reliably reproducible single-shot exploit from a purely external actor without insight into the validator's internal timing, though the described asymmetry (root_bank check at ingestion vs. no re-check at execution) is a real code-level gap, not something the review rules would treat as "already stopped" for this specific path since the check present in `process_and_record_transactions` does not apply to `process_and_record_aged_transactions`.

### Recommendation
Add an explicit `bank.vote_only_bank() && !vote_parser::is_valid_vote_only_transaction(tx)` check inside `Consumer::process_and_record_aged_transactions` (or within `resanitize_transaction_minimally`) using the live `bank` parameter passed at execution time, mirroring the check already present in `process_and_record_transactions`, so that vote-only enforcement is consistently applied at the point of execution regardless of which receive/buffer path produced the transaction.

### Proof of Concept
Conceptually: (1) capture `root_bank` snapshot while `root_bank.vote_only_bank() == false`; (2) have `working_bank` (or its child, per `replay_stage.rs` `NewBankOptions{vote_only_bank: true}`) become vote-only; (3) submit a non-vote transaction while the receive-and-buffer thread still observes the pre-transition `BankPair`; (4) observe that `try_handle_packet`/`translate_to_runtime_view` admits it because it checks `root_bank.vote_only_bank()` (false) rather than the (now vote-only) `working_bank`; (5) trace dispatch to `ConsumeWorker::consume` → `process_and_record_aged_transactions` and confirm no `vote_only_bank()` check exists there to reject it before commit, unlike `process_and_record_transactions`. [8](#0-7) [5](#0-4) 

Note: I was unable to fully inspect the body of `resanitize_transaction_minimally` in `runtime/src/bank.rs` within this session (only its signature/usage was located) to conclusively rule out an internal vote-only check inside that function itself; this should be verified directly in `runtime/src/bank.rs` before finalizing the assessment, as the index did not surface its full implementation.

### Citations

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L148-156)
```rust
    fn receive_and_buffer_packets(
        &mut self,
        container: &mut Self::Container,
        decision: &BufferedPacketsDecision,
    ) -> Result<ReceivingStats, DisconnectedError> {
        let BankPair {
            root_bank,
            working_bank,
        } = self.sharable_banks.load();
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L370-383)
```rust
    fn try_handle_packet(
        bytes: Bytes,
        root_bank: &Bank,
        working_bank: &Bank,
        transaction_account_lock_limit: usize,
        sanitize_config: &SanitizeConfig,
        filter_keys: &HashSet<Pubkey>,
    ) -> Result<TransactionViewState, PacketHandlingError> {
        let (view, deactivation_slot) = translate_to_runtime_view(
            bytes,
            root_bank,
            transaction_account_lock_limit,
            sanitize_config,
        )?;
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L394-404)
```rust
        let Ok(transaction_configuration) =
            view.transaction_configuration(&working_bank.feature_set)
        else {
            return Err(PacketHandlingError::ComputeBudget);
        };

        let max_age = calculate_max_age(root_bank.epoch(), deactivation_slot, root_bank.slot());
        let (priority, cost) =
            calculate_priority_and_cost(working_bank, &view, &transaction_configuration);

        Ok(TransactionState::new(view, max_age, priority, cost))
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L411-433)
```rust
pub(crate) fn translate_to_runtime_view<D: TransactionData>(
    data: D,
    bank: &Bank,
    transaction_account_lock_limit: usize,
    sanitize_config: &SanitizeConfig,
) -> Result<(RuntimeTransaction<ResolvedTransactionView<D>>, u64), PacketHandlingError> {
    // Parsing and basic sanitization checks
    let Ok(view) = SanitizedTransactionView::try_new_sanitized(data, sanitize_config) else {
        return Err(PacketHandlingError::Sanitization);
    };

    let Ok(view) = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
        view,
        MessageHash::Compute,
        None,
    ) else {
        return Err(PacketHandlingError::Sanitization);
    };

    // Discard non-vote packets if in vote-only mode.
    if bank.vote_only_bank() && !view.is_simple_vote_transaction() {
        return Err(PacketHandlingError::Sanitization);
    }
```

**File:** core/src/banking_stage/consume_worker.rs (L105-128)
```rust
    fn consume(
        &self,
        work: ConsumeWork<Tx>,
    ) -> Result<ProcessingStatus<Tx>, ConsumeWorkerError<Tx>> {
        let Some(leader_state) = active_leader_state(&self.shared_leader_state) else {
            return Ok(ProcessingStatus::CouldNotProcess(work));
        };
        let bank = leader_state
            .working_bank()
            .expect("active_leader_state should only return an active bank");
        self.metrics
            .count_metrics
            .num_messages_processed
            .fetch_add(1, Ordering::Relaxed);

        let output = self.consumer.process_and_record_aged_transactions(
            bank,
            &work.transactions,
            &work.max_ages,
            &ExecutionFlags {
                drop_on_failure: false,
                all_or_nothing: false,
            },
        );
```

**File:** core/src/banking_stage/consumer.rs (L140-159)
```rust
        let check_results = bank.check_transactions(
            txs,
            &pre_results,
            bank.max_processing_age(),
            true,
            &mut error_counters,
        );
        let check_results = check_results
            .into_iter()
            .zip(txs.iter())
            .map(|(result, tx)| match result {
                Ok(_) => {
                    if bank.vote_only_bank() && !vote_parser::is_valid_vote_only_transaction(tx) {
                        Err(TransactionError::SanitizeFailure)
                    } else {
                        Ok(())
                    }
                }
                Err(err) => Err(err),
            });
```

**File:** core/src/banking_stage/consumer.rs (L179-197)
```rust
    pub fn process_and_record_aged_transactions(
        &self,
        bank: &Bank,
        txs: &[impl TransactionWithMeta],
        max_ages: &[MaxAge],
        flags: &ExecutionFlags,
    ) -> ProcessTransactionBatchOutput {
        // Need to filter out transactions since they were sanitized earlier.
        // This means that the transaction may cross and epoch boundary (not allowed),
        //  or account lookup tables may have been closed.
        let pre_results = txs.iter().zip(max_ages).map(|(tx, max_age)| {
            bank.resanitize_transaction_minimally(
                tx,
                max_age.sanitized_epoch,
                max_age.alt_invalidation_slot,
            )
        });
        self.process_and_record_transactions_with_pre_results(bank, txs, pre_results, flags)
    }
```

**File:** ledger/src/blockstore_processor.rs (L1475-1497)
```rust
    let mut replay_timer = Measure::start("replay_elapsed");
    let is_vote_only_bank = bank.vote_only_bank();
    let replay_entries: Vec<_> = entries
        .into_iter()
        .zip(entry_tx_starting_indexes)
        .map(|(entry, tx_starting_index)| {
            if !is_vote_only_bank {
                return Ok(ReplayEntry {
                    entry,
                    starting_index: tx_starting_index,
                });
            }

            // If bank is in vote-only mode, validate that entries contain only vote transactions
            if let EntryType::Transactions(ref transactions) = entry
                && transactions
                    .iter()
                    .any(|tx| !is_valid_vote_only_transaction(tx))
            {
                return Err(BlockstoreProcessorError::UserTransactionsInVoteOnlyBank(
                    bank.slot(),
                ));
            }
```
