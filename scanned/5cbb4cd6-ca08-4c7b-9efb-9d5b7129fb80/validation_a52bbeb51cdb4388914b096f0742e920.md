## Title
Nonce authority check is hardcoded to `false` in `check_age_and_compute_budget_limits`, allowing transactions to bypass durable-nonce authorization during age validation - (File: `runtime/src/bank/check_transactions.rs`)

## Summary
`Bank::check_age_and_compute_budget_limits` calls `check_transaction_age` with the `strict_nonce_authority_check` argument hardcoded to `false`, instead of deriving/propagating it as an actual security decision the way `check_transaction_without_status_cache` does (which hardcodes `true` for both `strict_nonce_size_check` and `strict_nonce_authority_check`). This is structurally identical to the AdapterVault bug: a boolean flag that gates a critical authorization check is silently dropped/overridden with the "unsafe" default at one call site in the chain, while a sibling call site uses the safe value. [1](#0-0) [2](#0-1) 

## Finding Description
`check_nonce_transaction_validity` / `check_transaction_age` is the routine responsible for deciding whether a transaction with a stale `recent_blockhash` can still be considered valid because it references a durable nonce account. When `strict_nonce_authority_check` is `true`, the code enforces that the signer set of the nonce-advance instruction actually includes the nonce account's stored `authority`: [3](#0-2) 

In `check_age_and_compute_budget_limits` — the path used by ordinary transaction ingestion (`check_transactions` / `check_transactions_with_processed_slots` / `check_transactions_with_forwarding_delay`, used by banking stage's `consume_worker.rs` / `receive_and_buffer.rs`) — this call passes `false` for `strict_nonce_authority_check`: [4](#0-3) 

This means the age-check stage does not enforce that the signer of the "advance nonce" marker instruction is the actual `authority` recorded in the nonce account. By contrast, the leader-only helper `check_transaction_without_status_cache` explicitly passes `true` for both flags: [5](#0-4) 

The corrupted/weakened value here is the `strict_nonce_authority_check` boolean passed into `check_nonce_transaction_validity` at line 216 of `check_transactions.rs` — it is unconditionally `false` regardless of any transaction content, so the authority-signer check at lines 273-279 is entirely skipped for every transaction that goes through the normal `check_transactions` path.

## Impact Explanation
Because the nonce-authority check is bypassed in the mainline `check_transactions` path, any unprivileged user can construct a transaction whose `recent_blockhash` field equals the blockhash cached inside an *arbitrary* (not their own) durable-nonce account, and reference that nonce account as the durable-nonce marker instruction, without needing that account's authority to co-sign. `check_age_and_compute_budget_limits`/`check_transaction_age` will then treat the transaction as having a valid ("advanceable") blockhash purely because the nonce account's stored hash matches, letting an otherwise-expired/invalid transaction pass the "recent blockhash" validity gate that would normally reject it with `BlockhashNotFound`. This directly undermines the transaction-expiration/replay-protection invariant that `check_transactions` is supposed to enforce for every transaction entering the leader's pipeline, allowing an unprivileged actor to get transactions accepted for scheduling that should have been rejected — a false-acceptance vulnerability in the general transaction validity path used before execution.

## Likelihood Explanation
The flawed call site is on the default, always-invoked path for every incoming transaction (`check_transactions` → `check_transactions_with_processed_slots` → `check_age_and_compute_budget_limits`), used throughout banking stage transaction intake (`consumer.rs`, `consume_worker.rs`, `receive_and_buffer.rs`). No special privileges or trust assumptions are required — any user submitting a transaction can attempt to reference a public, readable nonce account's blockhash. This makes the likelihood of the broken invariant being reachable by an ordinary unprivileged user high, though I was unable to fully verify within available context whether downstream execution (system-program `advance_nonce_account` instruction processing or `account_loader.rs`'s handling of `CheckedTransactionDetails.nonce_address`) independently re-validates authority before any fee/rollback consequences occur — this would determine whether the bypass only causes spurious "false acceptance" at the check stage (a false execution/acceptance issue) or whether it can be leveraged into fee-payer/rollback abuse using another party's nonce state.

## Recommendation
Pass the intended `strict_nonce_authority_check` value (i.e., `true`, matching the leader-only helper) through `check_age_and_compute_budget_limits` into `check_transaction_age`/`check_nonce_transaction_validity`, rather than hardcoding `false`. Add a regression test asserting that a transaction referencing a durable nonce account it does not have authority over is rejected by `check_transactions`.

## Proof of Concept
1. Create nonce account `N` with `authority = A`, and note its stored blockhash `H_N` (nonce accounts are publicly readable).
2. As unprivileged attacker `B` (not `A`), craft a transaction:
   - `recent_blockhash = H_N`
   - First/marker instruction at `NONCED_TX_MARKER_IX_INDEX` is a `system_instruction::advance_nonce_account(N, B)` (signed only by `B`, not `A`).
3. Submit the transaction to a validator; it flows through `check_transactions` → `check_age_and_compute_budget_limits` → `check_transaction_age` → `check_nonce_transaction_validity`.
4. Because `strict_nonce_authority_check` is hardcoded `false` at [1](#0-0) 
the authority-signer check at [6](#0-5) 
is skipped, and the transaction is treated as having a valid nonce-based blockhash even though `B` is not `N`'s authority — bypassing the age/replay-protection check that would otherwise return `BlockhashNotFound`.

### Citations

**File:** runtime/src/bank/check_transactions.rs (L75-100)
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
```

**File:** runtime/src/bank/check_transactions.rs (L150-227)
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

                    Ok(CheckedTransactionDetails::new(
                        nonce_address,
                        compute_budget_and_limits,
                    ))
                }
                Err(e) => Err(e),
            })
            .collect()
    }
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
