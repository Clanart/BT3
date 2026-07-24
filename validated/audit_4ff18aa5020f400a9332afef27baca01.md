### Title
Narrow `is_rejecting_replacement_error` guard in `send_cpfp_tx` silently swallows child-tx failures, stalling CPFP-funded bridge transactions — (`crates/clementine-tx-sender/src/cpfp.rs`)

---

### Summary

`send_cpfp_tx` treats any `submitpackage` result that contains at least one "insufficient fee, rejecting replacement" error as a full success (`Ok(())`), even when the same result also contains unrelated failures for other transactions in the package. This is the Rust analog of the Solidity `catch Error(string memory)` pattern: one specific error class is recognised and causes a silent return, while all other error classes are masked. The consequence is that the parent bridge transaction can be left in the mempool without a fee-paying child, and the tx-sender records the attempt as successful, suppressing the normal retry/bump path for up to `fee_bump_after_blocks` blocks.

---

### Finding Description

In `send_cpfp_tx` (`crates/clementine-tx-sender/src/cpfp.rs`, lines 686–705), after calling `submitpackage`, the code iterates over per-transaction results and sets a boolean `has_replacement_error` if **any** failure message matches `is_rejecting_replacement_error` (`"insufficient fee, rejecting replacement"`). If that flag is set, the function immediately returns `Ok(())`, discarding all other errors that were also collected into `package_errors`:

```rust
// cpfp.rs lines 686-705
let mut package_errors = Vec::new();
let mut has_replacement_error = false;

for result in submit_result.tx_results.into_values() {
    if let PackageTransactionResult::Failure { error, .. } = result {
        if crate::rpc_errors::is_rejecting_replacement_error(&error) {
            has_replacement_error = true;
        }
        package_errors.push(error);   // other errors also collected
    }
}

if has_replacement_error {
    // returns Ok(()) even when package_errors contains non-replacement failures
    return Ok(());
}
```

`is_rejecting_replacement_error` is a single-string check:

```rust
// rpc_errors.rs line 15-17
pub(crate) fn is_rejecting_replacement_error(s: &str) -> bool {
    s.contains("insufficient fee, rejecting replacement")
}
```

A two-transaction CPFP package `[parent_tx, child_tx]` can produce mixed results from `submitpackage`:

| tx | result |
|----|--------|
| parent_tx | `Failure { error: "insufficient fee, rejecting replacement" }` — parent already in mempool |
| child_tx | `Failure { error: "bad-txns-inputs-missingorspent" }` — fee-payer UTXO already spent |

`has_replacement_error` becomes `true` → `Ok(())` is returned. The parent tx remains in the mempool **without** the child tx that was supposed to boost its package fee rate. The tx-sender records `effective_fee_rate` as updated (line 665) and considers the send successful.

---

### Impact Explanation

The CPFP fee-paying path is used for every critical bridge transaction type, including `MoveToVault`, `Kickoff`, `ReadyToReimburse`, `Round`, `WatchtowerChallenge`, `OperatorChallengeAck`, `ChallengeTimeout`, `DisproveTimeout`, `Reimburse`, `AssertTimeout`, `OptimisticPayout`, and others: [1](#0-0) [2](#0-1) 

When the silent-success path is taken:

1. The parent tx sits in the mempool at its original (low) fee rate with no child to boost it — miners have no incentive to include it.
2. The tx-sender has already written the new `effective_fee_rate` to the DB (line 665), so the "stuck for N blocks" bump logic uses the wrong baseline and may not trigger correctly.
3. On every subsequent task iteration the same package is re-submitted, the same mixed result is returned, and `Ok(())` is returned again — the error is never surfaced.
4. For time-sensitive transactions (kickoff, challenge-timeout, disprove-timeout, reimburse), a multi-block stall can cause the operator to miss a protocol window, exposing operator collateral to slashing or preventing reimbursement. [3](#0-2) 

---

### Likelihood Explanation

The mixed-result scenario arises whenever:

- The parent tx was submitted in a previous iteration and is already in the mempool (common during fee-bump cycles), **and**
- The child tx fails for a reason other than replacement — most concretely, the fee-payer UTXO was spent by a wallet-level transaction between the time it was confirmed and the time the child tx is constructed.

The `bump_fees_of_unconfirmed_fee_payer_txs` function does not mark a fee-payer UTXO as evicted when the fee-payer *creation* tx is confirmed (it only checks whether the creation tx is in the mempool or on-chain, not whether the UTXO output itself is still unspent): [4](#0-3) 

So the stall can persist across many iterations. Likelihood is **Medium** given normal operational conditions (fee-payer wallet reuse, RBF cycles, mempool churn).

---

### Recommendation

Return `Ok(())` only when **every** failure in the result set is a replacement error — i.e., the package was rejected solely because it was already present. If any non-replacement failure exists alongside a replacement error, propagate the error so the tx-sender can react correctly:

```rust
// proposed fix
if has_replacement_error && package_errors.len() == 1 {
    // Only the replacement error — parent already in mempool, treat as success
    return Ok(());
}

if !package_errors.is_empty() {
    return Err(SendTxError::Other(eyre!(
        "Failed to submit package: {:?}",
        package_errors
    )));
}
```

Alternatively, inspect which txid the replacement error belongs to: if it is the **parent** tx and the child tx succeeded, that is a genuine success; if the child tx also failed, it must be propagated.

---

### Proof of Concept

1. Operator has a `Kickoff` tx queued with `FeePayingType::CPFP`. It was submitted in a previous iteration and is now in the mempool.
2. Between iterations, the operator's Bitcoin wallet spends the confirmed fee-payer UTXO for an unrelated purpose.
3. On the next `try_to_send_unconfirmed_txs` call, `send_cpfp_tx` is invoked. `get_confirmed_fee_payer_utxos` returns the (now-spent) UTXO because `seen_at_height IS NOT NULL` in the DB.
4. `create_package` builds `[kickoff_tx, child_tx]` where `child_tx` spends the already-spent fee-payer UTXO.
5. `submitpackage` returns: `kickoff_tx → "insufficient fee, rejecting replacement"`, `child_tx → "bad-txns-inputs-missingorspent"`.
6. `has_replacement_error = true` → `return Ok(())`.
7. `effective_fee_rate` in the DB is updated to the current fee rate (line 665), resetting the stuck-tx counter.
8. Steps 3–7 repeat every task iteration. The kickoff tx never gets a fee-paying child and is never mined. The operator misses the challenge window. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/tx_sender_queue.rs (L57-91)
```rust
        match tx_type {
            TransactionType::Kickoff
            | TransactionType::Dummy
            | TransactionType::ChallengeTimeout
            | TransactionType::DisproveTimeout
            | TransactionType::Reimburse
            | TransactionType::Round
            | TransactionType::OperatorChallengeNack(_)
            | TransactionType::UnspentKickoff(_)
            | TransactionType::MoveToVault
            | TransactionType::BurnUnusedKickoffConnectors
            | TransactionType::KickoffNotFinalized
            | TransactionType::MiniAssert(_)
            | TransactionType::LatestBlockhashTimeout
            | TransactionType::LatestBlockhash
            | TransactionType::EmergencyStop
            | TransactionType::OptimisticPayout
            | TransactionType::ReadyToReimburse
            | TransactionType::ReplacementDeposit
            | TransactionType::WatchtowerChallenge(_)
            | TransactionType::AssertTimeout(_) => {
                // no_dependency and cpfp
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::CPFP,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** core/src/rpc/aggregator.rs (L2076-2095)
```rust
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    Some(TxMetadata {
                        deposit_outpoint: Some(deposit_outpoint),
                        operator_xonly_pk: None,
                        round_idx: None,
                        kickoff_idx: None,
                        tx_type: TransactionType::MoveToVault,
                    }),
                    &movetx,
                    FeePayingType::CPFP,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L468-484)
```rust
                Err(e) => {
                    // If not in mempool we should ignore, it was either evicted or replaced by a bumped feepayer tx
                    // give an error if the error is not "Transaction not in mempool"
                    if !e.to_string().contains("Transaction not in mempool") {
                        return Err(
                            eyre!("Failed to get mempool entry for {fee_payer_txid}: {e}").into(),
                        );
                    }
                    // get_transaction only returns if tx is wallet owned, it should not be an issue here as if it is not wallet owned,
                    // for example if wallet was changed and txsender restarted, it cannot be bumped anyway
                    if let Ok(tx_info) = self.rpc.get_transaction(&fee_payer_txid, None).await {
                        if tx_info.info.blockhash.is_some() && tx_info.info.confirmations > 0 {
                            not_evicted_ids.insert(parent_id);
                        }
                    }
                    continue;
                }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L661-705)
```rust
        // Save the effective fee rate before attempting to send
        // This ensures that even if the send fails, we track the attempt
        // so the 10-block stuck logic can trigger a bump
        self.db
            .update_effective_fee_rate(None, try_to_send_id, fee_rate, current_tip_height)
            .await
            .wrap_err("Failed to update effective fee rate")?;

        // Update sending state to submitting_package
        let _ = self
            .db
            .update_tx_debug_sending_state(try_to_send_id, "submitting_package", true)
            .await;

        let submit_result = self
            .rpc
            .submit_package(&package_refs, Some(Amount::ZERO), None)
            .await
            .wrap_err("Failed to submit package")?;

        // If tx_results is empty, it means the txs were already accepted by the network.
        if submit_result.tx_results.is_empty() {
            return Ok(());
        }

        let mut package_errors = Vec::new();
        let mut has_replacement_error = false;

        for result in submit_result.tx_results.into_values() {
            if let PackageTransactionResult::Failure { error, .. } = result {
                if crate::rpc_errors::is_rejecting_replacement_error(&error) {
                    has_replacement_error = true;
                }
                package_errors.push(error);
            }
        }

        if has_replacement_error {
            tracing::debug!(
                try_to_send_id,
                "Package tx rejected (tx already in mempool): {:?}",
                package_errors
            );
            return Ok(());
        }
```

**File:** crates/clementine-tx-sender/src/rpc_errors.rs (L15-17)
```rust
pub(crate) fn is_rejecting_replacement_error(s: &str) -> bool {
    s.contains("insufficient fee, rejecting replacement")
}
```
