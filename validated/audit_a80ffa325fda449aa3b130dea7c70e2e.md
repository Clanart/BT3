Audit Report

## Title
Unauthenticated durable-nonce transactions can evict/censor legitimate nonce transactions in the banking-stage mempool - (File: `runtime/src/bank/check_transactions.rs`)

## Summary
The leader-side age check `check_age_and_compute_budget_limits` calls `check_transaction_age` with `strict_nonce_authority_check` hardcoded to `false` [1](#0-0) , which flows into `check_nonce_transaction_validity` and entirely skips the authority-signer match against `nonce_data.authority` when this flag is false [2](#0-1) . This means a transaction naming a victim's nonce account but signed by an unrelated attacker (not the nonce authority) is still accepted by the leader as a valid pending nonce transaction keyed to that nonce address, and can then participate in the scheduler's nonce-dedup eviction logic, which evicts a lower-priority buffered transaction sharing the same nonce address [3](#0-2) .

## Finding Description
`check_age_and_compute_budget_limits` is the function used by both `check_transactions` and `check_transactions_with_processed_slots` — the leader-side paths that decide whether transactions are buffered — and it always passes `false` as `strict_nonce_authority_check` to `check_transaction_age` [4](#0-3) . Inside `check_nonce_transaction_validity`, the authority match against `message.get_ix_signers(NONCED_TX_MARKER_IX_INDEX)` is gated entirely behind `strict_nonce_authority_check`, so with it disabled, any signer — not necessarily the recorded nonce authority — is accepted, and the function returns `Some((nonce_address, ...))` regardless [2](#0-1) .

Notably, there is a separate, stricter function, `check_transaction_without_status_cache`, which explicitly passes `true` for both `strict_nonce_size_check` and `strict_nonce_authority_check` [5](#0-4) , and its own doc comment says "This is a leader-only function and must not be used in replay without a feature gate." This confirms that a strict-authority variant exists and is deliberately used elsewhere, while the buffering/dedup path uses the loose variant.

By contrast, actual execution-time validation in `check_load_and_advance_message_nonce_account` in the SVM does require `nonce_authority_is_valid` (the signer matches `nonce_data.authority`) before advancing the nonce, and rejects with `BlockhashNotFound` otherwise [6](#0-5) . This confirms the on-chain authority check is real and enforced at execution — the gap exists specifically in the leader-side buffering/dedup admission path.

The scheduler's receive/buffer path does deduplicate incoming transactions by nonce address and evicts a lower-priority buffered transaction in favor of a higher-priority incoming one for the same nonce address, as directly demonstrated by the `test_receive_and_buffer_nonce_dedup_drop_evict` test case (`lohi_evict`) [3](#0-2) .

## Impact Explanation
This is a non-RPC, unprivileged transaction-censorship primitive at the banking-stage/TPU layer. An attacker with no relationship to a victim's nonce account (no authority key, only public account data) can craft a cheap, higher-priority-fee transaction naming the victim's nonce account and their own pubkey as the (unauthorized) authority argument. Because the leader's admission check does not verify the authority match, this bogus transaction is accepted into the buffer as a legitimate nonce transaction for that nonce address, and per the demonstrated eviction behavior, it can evict the victim's real, correctly-signed, lower-priority-fee transaction from the buffer — even though the attacker's transaction can never itself succeed on-chain (it will fail at `check_load_and_advance_message_nonce_account`). This matches "false-non-acceptance/transaction-censorship" impact against durable-nonce transaction senders, which is a valid impact category for unprivileged issues in the transactions/runtime path.

## Likelihood Explanation
The nonce account pubkey and its stored `authority`/durable-nonce hash are fully public account data, readable by anyone. The attacker's only cost is a competitive priority fee; the malicious transaction never needs to succeed on-chain, it only needs to pass the loosened age check to occupy/compete for the nonce-address dedup slot. No privileged role, leaked key, or malicious-validator assumption is required, and the attack is repeatable indefinitely and cheaply against any address using durable nonces.

## Recommendation
Use the strict nonce-authority check (or equivalent authority verification) in the leader-side buffering/dedup admission path (`check_age_and_compute_budget_limits`/`check_transaction_age`), or have the nonce-dedup eviction logic in `receive_and_buffer.rs` re-verify the authority signer before allowing an incoming transaction to occupy or evict the dedup slot for a given nonce address, so spoofed-authority transactions cannot be used to censor legitimately-signed nonce transactions.

## Proof of Concept
1. Read victim's nonce account `N` (System Program owned) — its public `authority` `A` and current durable-nonce hash `H`.
2. Craft `T_attack`: instruction 0 = `advance_nonce_account(N, attacker_pubkey)` (naming the attacker, not `A`, as authority), `recent_blockhash = H`, signed only by the attacker, with a higher `compute_unit_price` than the victim's pending transaction.
3. Submit `T_attack` to the leader. `check_age_and_compute_budget_limits` → `check_transaction_age` → `check_nonce_transaction_validity` runs with `strict_nonce_authority_check = false` [1](#0-0) , so `T_attack` is accepted as valid, keyed to `N`.
4. Per the eviction logic validated by `test_receive_and_buffer_nonce_dedup_drop_evict`'s `lohi_evict` case, the victim's lower-fee legitimate transaction for `N` is evicted from the buffer [3](#0-2) .
5. `T_attack` subsequently fails at execution time because `nonce_authority_is_valid` is false in `check_load_and_advance_message_nonce_account` [6](#0-5) , but the victim's legitimate transaction has already been displaced and must be resubmitted, and the attack can be repeated at negligible cost.

### Citations

**File:** runtime/src/bank/check_transactions.rs (L75-101)
```rust
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

**File:** runtime/src/bank/check_transactions.rs (L150-217)
```rust
    fn check_age_and_compute_budget_limits<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        lock_results: impl IntoIterator<Item = TransactionResult<()>>,
        max_age: usize,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> Vec<TransactionCheckResult> {
        let hash_queue = self.blockhash_queue.read().unwrap();
        let next_durable_nonce = hash_queue.next_durable_nonce();

        let feature_set: &FeatureSet = &self.feature_set;
        let feature_snapshot = feature_set.snapshot();
        let fee_features = self.fee_features();

        let raise_cpi_limit = feature_snapshot.raise_cpi_nesting_limit_to_8;

        sanitized_txs
            .iter()
            .zip(lock_results)
            .map(|(tx, lock_res)| match lock_res {
                Ok(()) => {
                    let compute_budget_and_limits = tx
                        .borrow()
                        .transaction_configuration(feature_set)
                        .map(|config| {
                            let fee_details = calculate_fee_details(
                                tx.borrow(),
                                self.fee_structure.lamports_per_signature,
                                config.priority_fee_lamports,
                                fee_features,
                            );
                            if let Some(compute_budget) = self.compute_budget {
                                // This block of code is only necessary to retain legacy behavior of the code.
                                // It should be removed along with the change to favor transaction's compute budget limits
                                // over configured compute budget in Bank.
                                compute_budget.get_compute_budget_and_limits(
                                    config.loaded_accounts_data_size_limit,
                                    fee_details,
                                )
                            } else {
                                SVMTransactionExecutionAndFeeBudgetLimits {
                                    budget: SVMTransactionExecutionBudget {
                                        compute_unit_limit: u64::from(config.compute_unit_limit),
                                        heap_size: config.updated_heap_bytes,
                                        ..SVMTransactionExecutionBudget::new_with_defaults(
                                            raise_cpi_limit,
                                        )
                                    },
                                    loaded_accounts_data_size_limit: config
                                        .loaded_accounts_data_size_limit,
                                    fee_details,
                                }
                            }
                        })
                        .inspect_err(|_err| {
                            error_counters.invalid_compute_budget += 1;
                        })?;

                    let nonce_address = self.check_transaction_age(
                        tx.borrow(),
                        max_age,
                        &next_durable_nonce,
                        &hash_queue,
                        error_counters,
                        strict_nonce_size_check,
                        false,
                    )?;
```

**File:** runtime/src/bank/check_transactions.rs (L258-284)
```rust
    pub(super) fn check_nonce_transaction_validity(
        &self,
        message: &impl SVMMessage,
        next_durable_nonce: &DurableNonce,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> Option<(Pubkey, u64)> {
        let nonce_is_advanceable = message.recent_blockhash() != next_durable_nonce.as_hash();
        if !nonce_is_advanceable {
            return None;
        }

        let (nonce_address, nonce_data) =
            self.load_message_nonce_data(message, strict_nonce_size_check)?;

        if strict_nonce_authority_check
            && !message
                .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
                .any(|signer| signer == &nonce_data.authority)
        {
            return None;
        }

        let previous_lamports_per_signature = nonce_data.get_lamports_per_signature();

        Some((nonce_address, previous_lamports_per_signature))
    }
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L1428-1454)
```rust
    // a higher priority incoming nonce transaction evicts the existing transaction,
    // a lower or equal priority incoming nonce transaction is dropped
    #[test_case(HIGH_FEE, LOW_FEE; "hilo_drop")]
    #[test_case(HIGH_FEE, HIGH_FEE; "hihi_drop")]
    #[test_case(LOW_FEE, HIGH_FEE; "lohi_evict")]
    fn test_receive_and_buffer_nonce_dedup_drop_evict(old_fee: u64, new_fee: u64) {
        let (sender, receiver) = bounded(1024);
        let (bank_forks, mint_keypair) = test_bank_forks_with_fee();
        let (mut receive_and_buffer, mut container) =
            setup_transaction_view_receive_and_buffer(receiver, bank_forks.clone());
        let (nonce_pubkey, durable) = create_nonce_identity(&bank_forks, &mint_keypair.pubkey());
        let new_has_priority = new_fee > old_fee;

        send_transactions(
            &sender,
            &[create_nonce_transaction(
                &mint_keypair,
                &nonce_pubkey,
                old_fee,
                durable,
            )],
        );
        assert_eq!(
            receive(&mut receive_and_buffer, &mut container).num_buffered,
            1
        );
        let prior_nonce_entry = *container
```

**File:** svm/src/transaction_processor.rs (L871-892)
```rust
        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```
