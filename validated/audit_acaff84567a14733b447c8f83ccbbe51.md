### Title
Payout OP_RETURN is trusted as sole credit key with no cross-check against the actual payout funder, permanently locking an honest operator out of reimbursement - (File: core/src/database/verifier.rs, core/src/task/payout_checker.rs)

### Summary
`get_first_unhandled_payout_by_operator_xonly_pk` and `PayoutCheckerTask::run_once` credit reimbursement for withdrawal index `i` solely based on the `payout_payer_operator_xonly_pk` column, which is populated exclusively from the OP_RETURN of whatever transaction is found on-chain spending the withdrawal UTXO. Given the stated precondition that the OP_RETURN stored for the canonical mined transaction can diverge from the operator who actually funded/broadcast the payout, the credited operator (B) and the real funder (A) are never the same identity, and `mark_payout_handled` makes the mismatch permanent.

### Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk` recorded for withdrawal idx `i` == the operator whose funds actually paid `output0` of the mined payout transaction for idx `i`.

The recording path is `update_finalized_payouts` in `core/src/verifier.rs`, which for each withdrawal idx takes the payout transaction that the bitcoin syncer determined to be the one spending the withdrawal UTXO on the canonical chain, extracts the OP_RETURN from that single transaction, and writes whatever xonly pubkey is embedded there into `payout_payer_operator_xonly_pk` with no independent verification that the OP_RETURN pubkey corresponds to the party who actually supplied/authorized the funding path of that same transaction: [1](#0-0) . This value is later consumed verbatim by `get_first_unhandled_payout_by_operator_xonly_pk`, which does a pure equality match on `payout_payer_operator_xonly_pk = $1` with no additional funder-verification join: [2](#0-1) .

`PayoutCheckerTask::run_once` then unconditionally trusts this match: it fetches the unhandled payout for the operator's own xonly pk, calls `Operator::handle_finalized_payout`, and finalizes with `mark_payout_handled(citrea_idx, kickoff_txid)`, which sets `is_payout_handled = TRUE` for that idx with no re-check of which party actually funded the transaction: [3](#0-2) , [4](#0-3) . Because `is_payout_handled` is a single boolean per withdrawal idx, once it flips TRUE for operator B's credit, `get_first_unhandled_payout_by_operator_xonly_pk(A.xonly_pk)` will never again surface idx `i` for operator A, since the `WHERE ... is_payout_handled = FALSE` predicate excludes it permanently for every operator.

This design assumes the OP_RETURN pubkey extracted by `update_finalized_payouts` faithfully identifies the funder of that exact confirmed transaction. Under the stated precondition (established via the referenced "input0+output0-race" transaction-malleability vector, not re-derived here), the mined transaction's `output0` value path is attributable to A while its OP_RETURN records B's pubkey; no code in this file or in `is_kickoff_malicious` cross-checks the funding path independently of the OP_RETURN — `is_kickoff_malicious` in `core/src/verifier.rs` also only compares the OP_RETURN-derived pubkey against the kickoff sender's claimed identity, not against any independent funder proof: [5](#0-4) .

### Impact Explanation
If the precondition holds, operator B is reimbursed for a payout it never funded (bridge value credited to the wrong party), while operator A — the genuine funder — is permanently blocked from ever claiming reimbursement for the same withdrawal index, since `is_payout_handled` has already flipped to TRUE. This matches the Critical categories "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed." The blast radius is per-withdrawal-index and repeatable across any withdrawal index and any pair of registered operators, so it scales with the number of withdrawals processed under the same underlying OP_RETURN-substitution precondition.

### Likelihood Explanation
This specific defect (trusting `payout_payer_operator_xonly_pk` with no funder cross-check, and making `is_payout_handled` a single irrevocable flag) requires no special preconditions beyond the referenced upstream OP_RETURN-substitution vector actually being achievable — that upstream mechanism (getting a transaction mined whose OP_RETURN differs from the party who supplied `output0`) is outside the two functions audited here and was not independently re-verified in this pass; I could not confirm within this investigation whether `operator.rs::withdraw()` enforces a sighash type that would prevent OP_RETURN substitution while preserving `output0`, as I did not reach the signature-verification portion of that function. Given the precondition, however, no code in `verifier.rs` or `payout_checker.rs` provides any additional barrier, so exploitation would be deterministic and repeatable once the upstream race succeeds.

### Recommendation
Do not derive reimbursement credit solely from the OP_RETURN payload. Bind the credited operator to the transaction's actual funding path (e.g., require the operator identity to be provable from the signature/witness structure that authorized spending the withdrawal UTXO, not merely from an unauthenticated OP_RETURN output), and/or require the withdrawal's presigned payout commitment to fix the OP_RETURN output value at signing time under a sighash flag that covers all outputs (SIGHASH_ALL) so it cannot be substituted without invalidating the authorizing signature.

### Proof of Concept
```rust
// core/src/task/payout_checker.rs (new test, requires the upstream OP_RETURN-substitution
// precondition to be independently demonstrated/injected into the withdrawals table)
#[tokio::test]
async fn payout_credited_to_wrong_operator_locks_out_real_funder() {
    // Setup: two operators A and B, one withdrawal idx `i`, one real Payout tx funded by A.
    // Precondition injection: directly call
    //   db.update_payout_txs_and_payer_operator_xonly_pk(None, vec![(i, real_payout_txid, Some(B.xonly_pk), blockhash)])
    // to simulate the confirmed transaction whose OP_RETURN records B while output0 was
    // funded via A's broadcast (per referenced input0+output0-race precondition).

    // Assert B's PayoutCheckerTask::run_once matches idx i and marks it handled.
    let mut b_task = PayoutCheckerTask::new(db.clone(), operator_b.clone());
    assert!(b_task.run_once().await.unwrap()); // returns true, credits B
    assert!(db.get_first_unhandled_payout_by_operator_xonly_pk(None, b_xonly_pk).await.unwrap().is_none());

    // Assert A's PayoutCheckerTask::run_once for the same idx never returns true again.
    let mut a_task = PayoutCheckerTask::new(db.clone(), operator_a.clone());
    assert!(!a_task.run_once().await.unwrap()); // false: idx i is_payout_handled=TRUE already, forever
}
```
Equality checked: `payout_payer_operator_xonly_pk(i)` (== B) vs. actual funder of `output0(i)` (== A) — mismatched before and after `mark_payout_handled`, with no code path reconciling them.

### Citations

**File:** core/src/verifier.rs (L1871-1890)
```rust
        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2321)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
```

**File:** core/src/database/verifier.rs (L286-296)
```rust
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));
```

**File:** core/src/database/verifier.rs (L348-360)
```rust
    pub async fn mark_payout_handled(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        kickoff_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals SET is_payout_handled = TRUE, kickoff_txid = $2 WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(kickoff_txid));

        execute_query_with_tx!(self.connection, tx, query, execute)?;
```

**File:** core/src/task/payout_checker.rs (L41-106)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
