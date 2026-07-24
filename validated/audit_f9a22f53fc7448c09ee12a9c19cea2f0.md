### Title
Silent Discard of Signing Errors for Critical Bridge Transactions Causes Permanent Operator Reimbursement Loss — (File: `core/src/builder/transaction/sign.rs`)

### Summary

In `create_and_sign_txs`, the `Result` returned by `tx_sign_and_fill_sigs` and `tx_sign_preimage` is unconditionally discarded with `let _ =`. If signing fails for a critical transaction such as `Reimburse`, `DisproveTimeout`, or `OperatorChallengeAck`, the transaction is silently absent from the returned `signed_txs` vector. The caller `handle_finalized_payout` only asserts the presence of `Kickoff`; it does not verify that `Reimburse` or the timeout transactions were signed and queued. The payout is then marked as handled in the database, permanently closing the reimbursement window while the operator's collateral remains locked in the bridge vault.

### Finding Description

`create_and_sign_txs` iterates over all transaction handlers and attempts to sign each one:

```rust
// core/src/builder/transaction/sign.rs  lines 162-166
let _ = signer
    .tx_sign_and_fill_sigs(&mut txhandler, &signatures, Some(&mut tweak_cache))
    .wrap_err(format!(
        "Couldn't sign transaction {tx_type:?} in create_and_sign_txs for context {context:?}"
    ));
``` [1](#0-0) 

A second silent discard occurs for `OperatorChallengeAck`:

```rust
// line 179
let _ = signer.tx_sign_preimage(&mut txhandler, preimage);
``` [2](#0-1) 

After the discard, `txhandler.promote()` is called. If signing failed, `promote()` returns `Err`, and the transaction is logged at `tracing::debug` level and excluded from `signed_txs` — with no error propagated to the caller. [3](#0-2) 

The caller `handle_finalized_payout` iterates `signed_txs` and enqueues only the transactions that are present:

```rust
for (tx_type, signed_tx) in &signed_txs {
    match *tx_type {
        TransactionType::Kickoff
        | TransactionType::OperatorChallengeAck(_)
        | TransactionType::WatchtowerChallengeTimeout(_)
        | TransactionType::ChallengeTimeout
        | TransactionType::DisproveTimeout
        | TransactionType::Reimburse => {
            self.tx_sender.add_tx_to_queue(...).await?;
        }
        _ => {}
    }
}
``` [4](#0-3) 

The only post-loop assertion is that `Kickoff` is present:

```rust
let kickoff_txid = signed_txs
    .iter()
    .find_map(|(tx_type, tx)| {
        if let TransactionType::Kickoff = tx_type {
            Some(tx.compute_txid())
        } else {
            None
        }
    })
    .ok_or(eyre::eyre!("Couldn't find kickoff tx in signed_txs"))?;
``` [5](#0-4) 

If `Kickoff` signed correctly but `Reimburse` did not, the function returns `Ok(kickoff_txid)`. The `PayoutCheckerTask` then calls `mark_payout_handled`, permanently closing the reimbursement window:

```rust
self.db
    .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
    .await?;
dbtx.commit().await?;
``` [6](#0-5) 

### Impact Explanation

The operator fronts BTC from their own wallet to fulfill a withdrawal. The `Reimburse` transaction is the sole mechanism by which the operator recovers those funds from the bridge vault. If `Reimburse` is silently absent from the tx-sender queue and the payout is marked handled, the operator's collateral is permanently locked — a direct, irreversible loss of bridged BTC value from the operator's perspective. Similarly, if `DisproveTimeout` is silently dropped, a verifier can execute a disprove and slash the operator's collateral without the operator being able to defend.

### Likelihood Explanation

`tx_sign_and_fill_sigs` fails when the pre-computed deposit-phase signatures stored in the database are absent, malformed, or mismatched (e.g., wrong round/kickoff index, DB row missing after a partial write, schema migration, or reorg-triggered re-processing). These are realistic operational conditions, not theoretical ones. The silent discard means the failure is invisible in logs above `DEBUG` level, making it hard to detect until the operator notices missing reimbursements.

### Recommendation

Propagate signing errors for transactions that are required for bridge safety. Replace the `let _ =` pattern with `?` (or an explicit `return Err(...)`) for `Reimburse`, `DisproveTimeout`, `ChallengeTimeout`, and `OperatorChallengeAck`. After the loop in `handle_finalized_payout`, assert that all mandatory transaction types are present in `signed_txs` before committing the database transaction and marking the payout as handled. The intentional "skip if not signable" behavior (valid for optional transactions) should be expressed with an explicit allowlist rather than a blanket silent discard.

### Proof of Concept

1. Operator calls `withdraw()`, successfully broadcasting the payout transaction.
2. `PayoutCheckerTask::run_once` detects the unhandled payout and calls `handle_finalized_payout`.
3. `create_and_sign_txs` is invoked. Suppose the deposit-phase signatures for `Reimburse` are absent from the DB (e.g., due to a partial write during deposit finalization).
4. `tx_sign_and_fill_sigs` returns `Err(...)` for the `Reimburse` handler. The error is discarded by `let _ =`.
5. `txhandler.promote()` returns `Err` (unsigned inputs). The `Reimburse` entry is logged at `DEBUG` and excluded from `signed_txs`.
6. `Kickoff` signed correctly, so `find_map` returns `Some(kickoff_txid)`.
7. `handle_finalized_payout` returns `Ok(kickoff_txid)`. `mark_payout_handled` commits.
8. The `Reimburse` transaction is never in the tx-sender queue. The operator's collateral is permanently locked in the bridge vault with no recovery path.

### Citations

**File:** core/src/builder/transaction/sign.rs (L162-166)
```rust
        let _ = signer
            .tx_sign_and_fill_sigs(&mut txhandler, &signatures, Some(&mut tweak_cache))
            .wrap_err(format!(
                "Couldn't sign transaction {tx_type:?} in create_and_sign_txs for context {context:?}"
            ));
```

**File:** core/src/builder/transaction/sign.rs (L179-179)
```rust
            let _ = signer.tx_sign_preimage(&mut txhandler, preimage);
```

**File:** core/src/builder/transaction/sign.rs (L197-211)
```rust
        let checked_txhandler = txhandler.promote();

        match checked_txhandler {
            Ok(checked_txhandler) => {
                signed_txs.push((tx_type, checked_txhandler.get_cached_tx().clone()));
            }
            Err(e) => {
                tracing::debug!(
                    "Couldn't sign transaction {:?} in create_and_sign_all_txs: {:?}.
                    This might be normal if the transaction is not needed to be/cannot be signed.",
                    tx_type,
                    e
                );
            }
        }
```

**File:** core/src/operator.rs (L926-949)
```rust
        for (tx_type, signed_tx) in &signed_txs {
            match *tx_type {
                TransactionType::Kickoff
                | TransactionType::OperatorChallengeAck(_)
                | TransactionType::WatchtowerChallengeTimeout(_)
                | TransactionType::ChallengeTimeout
                | TransactionType::DisproveTimeout
                | TransactionType::Reimburse => {
                    #[cfg(feature = "automation")]
                    self.tx_sender
                        .add_tx_to_queue(
                            dbtx,
                            *tx_type,
                            signed_tx,
                            &signed_txs,
                            tx_metadata,
                            self.config.protocol_paramset(),
                            None,
                        )
                        .await?;
                }
                _ => {}
            }
        }
```

**File:** core/src/operator.rs (L951-962)
```rust
        let kickoff_txid = signed_txs
            .iter()
            .find_map(|(tx_type, tx)| {
                if let TransactionType::Kickoff = tx_type {
                    Some(tx.compute_txid())
                } else {
                    None
                }
            })
            .ok_or(eyre::eyre!(
                "Couldn't find kickoff tx in signed_txs".to_string(),
            ))?;
```

**File:** core/src/task/payout_checker.rs (L104-108)
```rust
        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;
```
