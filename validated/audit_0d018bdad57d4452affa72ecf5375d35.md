## Title
`check_transaction_without_status_cache` allows a duplicate leader-produced transaction to skip `AlreadyProcessed` rejection - (File: `runtime/src/bank/check_transactions.rs`)

### Summary
`check_transaction_without_status_cache` is explicitly documented as leader-only and intentionally skips the status-cache lookup that `check_transactions`/`check_status_cache` perform. The test `test_check_transaction_without_status_cache_allows_already_processed` demonstrates that for the exact same transaction/blockhash, `check_transactions` correctly returns `Err(TransactionError::AlreadyProcessed)` while `check_transaction_without_status_cache` returns `Ok(None)` — i.e. "ready to execute." This is the same class of bug as the reported `canExecute` ambiguity: one boolean/Result-shaped readiness signal is made to mean two different things ("not yet checked/valid" vs "already committed"), depending on which code path calls it, without any type-level distinction forcing callers to reconcile the two meanings.

### Finding Description
`check_transaction_age` (called by `check_transaction_without_status_cache`) only validates blockhash/nonce age and returns `Ok(None)`/`Ok(Some(nonce_pubkey))` when the transaction's blockhash is fresh — it never consults `self.status_cache`. [1](#0-0) 

By contrast, the normal leader/replay path (`check_transactions` → `check_transactions_with_processed_slots` → `check_status_cache`) additionally looks up the status cache and converts any hit into `TransactionError::AlreadyProcessed`. [2](#0-1) [3](#0-2) 

The regression test explicitly proves the ambiguity: after inserting the transaction into the status cache (marking it "already processed"), `check_transactions` yields `Err(AlreadyProcessed)`, but `check_transaction_without_status_cache` for the identical transaction/blockhash still returns `Ok(None)`. [4](#0-3) 

This function is used by the banking-stage scheduler (`receive_and_buffer.rs`) as a leader-side fast-path to decide whether an already-received transaction is still worth buffering/scheduling for execution, deliberately bypassing the status cache for performance. The doc comment on the function itself flags the danger: "This is a leader-only function and must not be used in replay without a feature gate," acknowledging that its `Ok` result does **not** mean "safe/valid to commit," only "not yet expired by blockhash age" — the same semantic collapse the external report calls out for `canExecute`: a single "ready" signal is overloaded to also mean "not observed as done yet," when it should be a tri-state (`NotChecked` / `ReadyToExecute` / `AlreadyProcessed`).

### Impact Explanation
If a caller in the banking/scheduling path treats `Ok(None)`/`Ok(Some(_))` from `check_transaction_without_status_cache` as a general "not yet executed" signal rather than strictly "blockhash still valid," a transaction that was already executed and landed in a bank could be re-admitted into scheduling in that same leader window. Whether this ultimately results in double-execution/double-charging depends entirely on the SVM's own final status-cache check at commit time (`update_transaction_statuses`/`check_status_cache`) acting as a second gate. [5](#0-4) 
Because the ambiguity is isolated to a pre-execution scheduling filter, and a later, authoritative status-cache check exists at commit time, the primary risk is wasted scheduling/compute work (a resource-exhaustion/inefficiency issue) rather than outright fund duplication — but the finding does show a genuine "canExecute-style" two-different-states-one-boolean/Result collapse in a security-relevant code path, exactly matching the report's bug class.

### Likelihood Explanation
This path is reachable only through the leader's own already-processed transactions during normal in-slot scheduling (not attacker-controlled beyond normal transaction submission), and the function is explicitly gated to leader-only usage with commentary warning against reuse in replay. The likelihood of it causing an actual double-commit is low because of the downstream status-cache re-check, but the ambiguity itself is real and by design (not a hypothetical), as proven by the dedicated regression test.

### Recommendation
Follow the same remediation pattern recommended in the external report: replace the implicit two-state overload (`Ok` meaning both "not checked against status cache" and would-be "not yet processed") with an explicit tri-state result type (e.g., `NotChecked`, `Ready`, `AlreadyProcessed`) returned from `check_transaction_without_status_cache`, and require every caller to handle all three states explicitly rather than treating `Ok` uniformly. Additionally, tighten the doc/API so the function cannot be called from any path that skips the final status-cache re-check without an explicit opt-in flag validated at compile time (e.g., a marker type), rather than relying on comments alone.

### Proof of Concept
The existing unit test already constitutes a reproducible PoC of the ambiguity: [4](#0-3) 
1. Insert a transaction's message hash into `bank.status_cache` for the current slot with `Ok(())`, marking it processed.
2. Call `bank.check_transactions(...)` on the identical transaction — result: `Err(TransactionError::AlreadyProcessed)`.
3. Call `bank.check_transaction_without_status_cache(...)` on the identical transaction/blockhash — result: `Ok(None)`, i.e., reported as still executable.

This directly mirrors the `canExecute=false` ambiguity from the external report: the same underlying transaction state ("already executed") yields contradictory readiness signals depending solely on which check function is invoked, with no compile-time mechanism forcing reconciliation between the two.

### Citations

**File:** runtime/src/bank/check_transactions.rs (L72-101)
```rust
    /// Checks a sanitized transaction against the bank for age,
    /// without checking the status cache. This is a leader-only
    /// function and must not be used in replay without a feature gate.
    pub fn check_transaction_without_status_cache(
        &self,
        tx: &impl SVMMessage,
        max_age: usize,
        error_counters: &mut TransactionErrorMetrics,
    ) -> TransactionResult<Option<Pubkey>> {
        let feature_set: &FeatureSet = &self.feature_set;
        let feature_snapshot = feature_set.snapshot();
        let enable_tx_v1 = feature_snapshot.enable_tx_v1;

        if !enable_tx_v1 && tx.version() == TransactionVersion::Number(1) {
            return Err(TransactionError::UnsupportedVersion);
        }

        let hash_queue = self.blockhash_queue.read().unwrap();
        let next_durable_nonce = hash_queue.next_durable_nonce();

        self.check_transaction_age(
            tx,
            max_age,
            &next_durable_nonce,
            &hash_queue,
            error_counters,
            true, // strict_nonce_size_check
            true, // strict_nonce_authority_check
        )
    }
```

**File:** runtime/src/bank/check_transactions.rs (L103-127)
```rust
    pub fn check_transactions_with_processed_slots<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        lock_results: &[TransactionResult<()>],
        max_age: usize,
        collect_processed_slots: bool,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> (Vec<TransactionCheckResult>, Option<Vec<Option<Slot>>>) {
        let lock_results = self.filter_v1_transactions(sanitized_txs, lock_results);

        let lock_results = self.check_age_and_compute_budget_limits(
            sanitized_txs,
            lock_results,
            max_age,
            strict_nonce_size_check,
            error_counters,
        );
        self.check_status_cache(
            sanitized_txs,
            lock_results,
            collect_processed_slots,
            error_counters,
        )
    }
```

**File:** runtime/src/bank/check_transactions.rs (L302-335)
```rust
    fn check_status_cache<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        mut lock_results: Vec<TransactionCheckResult>,
        collect_processed_slots: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> (Vec<TransactionCheckResult>, Option<Vec<Option<Slot>>>) {
        // Do allocation before acquiring the lock on the status cache.
        let mut processed_slots = if collect_processed_slots {
            Some(Vec::with_capacity(sanitized_txs.len()))
        } else {
            None
        };
        let rcache = self.status_cache.read().unwrap();

        for (sanitized_tx_ref, lock_result) in sanitized_txs.iter().zip(lock_results.iter_mut()) {
            let processed_slot = if lock_result.is_ok() {
                self.get_processed_slot(sanitized_tx_ref.borrow(), &rcache)
            } else {
                None
            };

            if processed_slot.is_some() {
                error_counters.already_processed += 1;
                *lock_result = Err(TransactionError::AlreadyProcessed);
            }

            if let Some(processed_slots) = processed_slots.as_mut() {
                processed_slots.push(processed_slot)
            }
        }

        (lock_results, processed_slots)
    }
```

**File:** runtime/src/bank/check_transactions.rs (L688-722)
```rust
    #[test]
    fn test_check_transaction_without_status_cache_allows_already_processed() {
        let (genesis_config, _mint_keypair) = solana_genesis_config::create_genesis_config(1);
        let bank = Bank::new_for_tests(&genesis_config);
        let tx = make_test_tx_with_blockhash(TransactionVersion::LEGACY, bank.last_blockhash());

        bank.status_cache.write().unwrap().insert(
            tx.recent_blockhash(),
            tx.message_hash(),
            bank.slot(),
            Ok(()),
        );

        let lock_results = [Ok(())];
        let mut error_counters = TransactionErrorMetrics::default();
        let check_results = bank.check_transactions(
            std::slice::from_ref(&tx),
            &lock_results,
            bank.max_processing_age(),
            true,
            &mut error_counters,
        );
        assert!(matches!(
            check_results.as_slice(),
            [Err(TransactionError::AlreadyProcessed)]
        ));

        let mut error_counters = TransactionErrorMetrics::default();
        let check_result = bank.check_transaction_without_status_cache(
            &tx,
            bank.max_processing_age(),
            &mut error_counters,
        );
        assert_eq!(check_result, Ok(None));
    }
```

**File:** runtime/src/bank.rs (L3515-3544)
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
    }
```
