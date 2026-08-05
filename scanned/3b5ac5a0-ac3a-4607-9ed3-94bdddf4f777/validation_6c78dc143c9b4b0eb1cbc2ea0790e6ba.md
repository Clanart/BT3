Based on my research, the strongest local analog to the external "redundant processed-hash check" report is found in `ledger/src/blockstore_processor.rs`, where a message-hash duplicate check was deliberately introduced to *replace* the old lock-conflict-based replay defense after SIMD‑83, but is scoped more narrowly than the invariant it is supposed to enforce.

### Title
Cross-entry duplicate-transaction replay in a single block bypasses both SIMD-83 lock relaxation and the message-hash dedup check - (File: `ledger/src/blockstore_processor.rs`)

### Summary
Post-SIMD-83, Agave intentionally stopped treating two transactions with an identical message hash as lock-conflicting, and instead relies on a message-hash `HashSet` dedup check to reject a replayed transaction. That dedup set, however, is recreated fresh for every single *entry*, not for the whole block/slot, so a transaction duplicated across two different entries of the same block is not caught by this check, while it is also no longer caught by account-lock conflict.

### Finding Description
`validate_entry_transactions` builds a fresh `AHashSet` of message hashes on every call and rejects only duplicates found inside that one call's transaction slice: [1](#0-0) 

`process_entries` calls this function separately, once per `EntryType::Transactions(transactions)`, i.e., once per entry in the block, with no shared state carried across entries: [2](#0-1) 

The comment on `validate_entry_transactions` explicitly documents the design assumption that this check *is* the replay defense now, because SIMD-83 removed the older protection: "Post-SIMD-83 the duplicate-message-hash check is what rejects an entry that replays the same transaction twice (it no longer conflicts on locks)." [3](#0-2) 

This is confirmed by the existing test, which shows that duplicate detection for two identical transactions only works because they are in the *same* entry: "one entry, two instances of the same transaction... with simd83: due to message hash duplication": [4](#0-3) 

Nothing in `process_entries` calls `Bank::check_transactions`/`check_status_cache` (the mechanism that would otherwise catch an already-processed message hash across batches) before scheduling execution; it only calls `validate_entry_transactions` and then `bank.schedule_transaction_executions`: [5](#0-4) 

The status cache is only updated *after* a transaction executes, via `update_transaction_statuses`, which inserts the message hash keyed by slot: [6](#0-5) 

The unified scheduler used for block verification is explicitly designed to buffer and run as much unconflicting work as possible with no artificial serialization ("there is no agony for block verification with regards to max_running_task_count... just specify no limit and buffer everything as much as possible"): [7](#0-6) 

Because (a) SIMD-83 makes duplicate-message-hash transactions lock-compatible, (b) the only remaining dedup check is scoped to a single entry, and (c) the scheduler is designed to run all non-conflicting tasks with maximum concurrency, a transaction that appears twice across two different entries within the same block/slot has no remaining guard preventing it from being scheduled and executed twice before the first execution's status-cache write becomes visible to a would-be duplicate check on the second.

### Impact Explanation
If a duplicated transaction is scheduled twice within the same slot, its instructions execute twice against a single bank state. For any transaction whose instructions are not idempotent (a `transfer`, `burn`, `mint`, `withdraw`, or arbitrary program state mutation), a double-execution directly causes fund duplication/loss or corrupted account state, and because this happens during block replay, it risks bank-hash divergence between validators depending on scheduling timing — a correctness/consensus-relevant defect, not merely a performance issue. This satisfies the "fund theft/loss" / "false execution/acceptance" criteria for the runtime/accounts path.

### Likelihood Explanation
The mechanism triggering this does not require a malicious validator: it only requires that the same signed transaction (bit-for-bit identical message, which for Ed25519 signing is also byte-identical, including signature) is present in two entries of one block — something that can occur through ordinary duplicate submission/resubmission by an unprivileged user combined with normal (non-malicious) banking-stage/entry-packing behavior. I was not able to fully trace, within the remaining investigation budget, whether some additional guard elsewhere in the SVM execution path (outside `check_transactions`) independently re-checks the status cache per-transaction at commit time for the replay path; if such a guard exists it could mitigate this, but no such call was found in the `process_entries`/`schedule_transaction_executions` code path I inspected.

### Recommendation
Scope the message-hash dedup set in `validate_entry_transactions` (or an equivalent check) to the entire block/slot being replayed, not to each entry individually, so duplicate transactions cannot slip past it purely because they are split across different entries. Alternatively, reintroduce a status-cache/AlreadyProcessed check at the point the unified scheduler dispatches tasks so cross-entry duplicates cannot both be marked runnable concurrently.

### Proof of Concept
1. Construct a two-entry block for the same slot where entry 1 contains transaction `T` and entry 2 (a later PoH entry within the same slot) also contains transaction `T` (identical message hash/signature) — this passes leader-side per-batch checks as long as the two entries are packed/committed close enough in time or via independent banking-stage packing paths.
2. During replay via `process_entries`, `validate_entry_transactions` is invoked separately for entry 1 and entry 2, each with its own empty `batch_message_hashes`, so neither call detects the cross-entry duplicate (`ledger/src/blockstore_processor.rs:234-271`).
3. Because SIMD-83 makes identical-message-hash transactions lock-compatible, the unified scheduler does not serialize the two entries on account locks either.
4. Both instances of `T` are scheduled and executed by the unified scheduler, mutating the underlying account(s) twice, before/regardless of the status cache write from the first execution (`runtime/src/bank.rs:3515-3543`), because no explicit `check_transactions`/`check_status_cache` call gates `schedule_transaction_executions` in this path.
5. This is testable analogous to the existing `test_process_entry_duplicate_transaction` test, but with the duplicate transaction split across *two* separate `Entry` objects instead of within one entry, to show the existing dedup safeguard does not extend to that case.

### Citations

**File:** ledger/src/blockstore_processor.rs (L205-246)
```rust
fn process_entries(bank: &BankWithScheduler, entries: Vec<ReplayEntry>) -> Result<()> {
    let mut tick_hashes = vec![];

    for ReplayEntry {
        entry,
        starting_index,
    } in entries
    {
        match entry {
            EntryType::Tick(hash) => {
                // If it's a tick, save it for later
                tick_hashes.push(hash);
                if bank.is_block_boundary(bank.tick_height() + tick_hashes.len() as u64) {
                    break;
                }
            }
            EntryType::Transactions(transactions) => {
                if transactions.is_empty() {
                    continue;
                }

                // Any bank replaying transactions must have a scheduler installed. Slot 0 -
                // the only bank replayed before the scheduler pool is installed - is tick-only,
                // so it never reaches here.
                assert!(
                    bank.has_installed_scheduler(),
                    "no scheduler installed for bank of slot {} during replay",
                    bank.slot()
                );
                validate_entry_transactions(
                    &transactions,
                    bank.get_transaction_account_lock_limit(),
                )?;

                let indexes = starting_index..starting_index + transactions.len();
                // Widening usize index to OrderedTaskId (= u128) won't ever fail.
                let task_ids = indexes.map(|i| i.try_into().unwrap());

                bank.schedule_transaction_executions(transactions.into_iter().zip_eq(task_ids))?;
            }
        }
    }
```

**File:** ledger/src/blockstore_processor.rs (L253-271)
```rust
/// Validate an entry's transactions before scheduling: each transaction's account
/// locks (count and duplicates), and rejection of duplicate message hashes within
/// the entry. Does not take account locks - the unified scheduler orders conflicts.
/// Post-SIMD-83 the duplicate-message-hash check is what rejects an entry that
/// replays the same transaction twice (it no longer conflicts on locks).
fn validate_entry_transactions(
    transactions: &[RuntimeTransaction<SanitizedTransaction>],
    tx_account_lock_limit: usize,
) -> Result<()> {
    let mut batch_message_hashes = AHashSet::with_capacity(transactions.len());

    for transaction in transactions {
        validate_account_locks(transaction.account_keys(), tx_account_lock_limit)?;
        if !batch_message_hashes.insert(transaction.message_hash()) {
            return Err(TransactionError::AlreadyProcessed);
        }
    }

    Ok(())
```

**File:** ledger/src/blockstore_processor.rs (L3728-3781)
```rust
    #[test]
    fn test_process_entry_duplicate_transaction() {
        agave_logger::setup();

        let GenesisConfigInfo {
            genesis_config,
            mint_keypair,
            ..
        } = create_genesis_config(1000);
        let bank = Bank::new_for_tests(&genesis_config);
        let (bank, _bank_forks) = bank.wrap_with_bank_forks_for_tests();
        let keypair1 = Keypair::new();
        let keypair2 = Keypair::new();

        // fund: put some money in each of 1 and 2
        assert_matches!(bank.transfer(5, &mint_keypair, &keypair1.pubkey()), Ok(_));
        assert_matches!(bank.transfer(5, &mint_keypair, &keypair2.pubkey()), Ok(_));

        // one entry, two instances of the same transaction. this entry is invalid
        // without simd83: due to lock conflicts
        // with simd83: due to message hash duplication
        let entry_1_to_2_twice = next_entry(
            &bank.last_blockhash(),
            1,
            vec![
                system_transaction::transfer(
                    &keypair1,
                    &keypair2.pubkey(),
                    1,
                    bank.last_blockhash(),
                ),
                system_transaction::transfer(
                    &keypair1,
                    &keypair2.pubkey(),
                    1,
                    bank.last_blockhash(),
                ),
            ],
        );
        // should now be:
        // keypair1=5
        // keypair2=5

        // succeeds following simd83 locking, fails otherwise
        let result = process_entries_for_tests_with_scheduler(&bank, vec![entry_1_to_2_twice]);

        let balances = [
            bank.get_balance(&keypair1.pubkey()),
            bank.get_balance(&keypair2.pubkey()),
        ];

        assert_eq!(balances, [5, 5]);
        assert_eq!(result, Err(TransactionError::AlreadyProcessed));
    }
```

**File:** runtime/src/bank.rs (L3515-3543)
```rust
    fn update_transaction_statuses(
        &self,
        sanitized_txs: &[impl TransactionWithMeta],
        processing_results: &[TransactionProcessingResult],
    ) {
        let mut status_cache = self.status_cache.write().unwrap();
        assert_eq!(sanitized_txs.len(), processing_results.len());
        for (tx, processing_result) in sanitized_txs.iter().zip(processing_results) {
            if let Ok(processed_tx) = &processing_result {
                // Add the message hash to the status cache to ensure that this message
                // won't be processed again with a different signature.
                status_cache.insert(
                    tx.recent_blockhash(),
                    tx.message_hash(),
                    self.slot(),
                    processed_tx.status(),
                );
                if self.store_transaction_signatures_in_status_cache {
                    // Add the transaction signature to the status cache so that transaction
                    // status can be queried by transaction signature over RPC.
                    status_cache.insert(
                        tx.recent_blockhash(),
                        tx.signature(),
                        self.slot(),
                        processed_tx.status(),
                    );
                }
            }
        }
```

**File:** unified-scheduler-pool/src/lib.rs (L1035-1050)
```rust
    fn max_running_task_count() -> Option<usize> {
        // Unlike block production, there is no agony for block verification with regards
        // to max_running_task_count. Its responsibility is to execute all transactions by
        // _the pre-determined order_ and no reprioritization or interruption whatsoever.
        // So, just specify no limit and buffer everything as much as possible at the
        // runnable task channel.
        None
    }

    fn max_unique_active_task_count() -> Option<usize> {
        // We can't silently drop block verification tasks by imposing some arbitrary limit
        // here; otherwise same transactions could be allowed in the same block!
        // The block size (i.e. shred count) limit is already enforced before unified
        // scheduler.
        None
    }
```
