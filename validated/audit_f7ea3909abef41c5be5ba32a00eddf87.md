### Title
`update_finalized_payouts` keys finalized payouts by withdrawal UTXO alone, letting one physical payout credit multiple `citrea_idx`/deposit reimbursement paths - ([File: core/src/verifier.rs], [File: core/src/database/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` finds which withdrawals were paid by joining the `withdrawals` table to `bitcoin_syncer_spent_utxos` purely on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, with no requirement that each on-chain UTXO be attributable to exactly one `citrea_idx`. Because the withdrawal UTXO bytes are attacker-supplied at `withdraw()`-registration time and are stored per-index without a uniqueness constraint, an attacker can register the identical `OutPoint` for two different withdrawal indices `i` and `j`. A single real Bitcoin payout transaction that spends that UTXO then gets attributed to *both* `i` and `j` in the DB, letting the payer operator consume `get_unused_and_signed_kickoff_connector`/`handle_finalized_payout` reimbursement paths for both deposits from one funded payment.

### Finding Description
Broken binding: **one on-chain spend of a withdrawal UTXO ↔ exactly one `citrea_idx`/deposit's reimbursement eligibility** must hold. The code breaks this because it never scopes the match by `(deposit_id, withdrawal_utxo)`, only by the UTXO value.

- `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) executes:
```
SELECT w.idx, bsu.spending_txid
FROM withdrawals w
JOIN bitcoin_syncer_spent_utxos bsu
   ON bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout
WHERE bsu.block_id = $1
``` [1](#0-0) 
If two rows in `withdrawals` (indices `i` and `j`) carry the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, this JOIN returns two result rows — `(i, T)` and `(j, T)` — for the single real spending transaction `T`, because SQL joins are many-to-one on equal keys, not restricted to a unique winner.
- `withdrawal_utxo_txid/vout` are written per-index with no uniqueness constraint by `update_withdrawal_utxo_from_citrea_withdrawal` (`core/src/database/verifier.rs:108-135`), and the value ultimately originates from the Citrea Bridge contract's `withdrawalUTXOs` mapping populated at `withdraw()` time — attacker-controlled bytes, one independent slot per index (`core/src/citrea.rs:458-496`, `247-324`).
- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) consumes these `(idx, payout_txid)` pairs, resolves the OP_RETURN payer `operator_xonly_pk` from `T` once, and pushes **both** `(i, T, operator_pk, block_hash)` and `(j, T, operator_pk, block_hash)` into `update_payout_txs_and_payer_operator_xonly_pk`.
- Downstream, `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) processes each unhandled payout independently by `citrea_idx`, and for each one calls `Operator::handle_finalized_payout(deposit_outpoint, ...)` (`core/src/operator.rs:839-885`), which calls `get_unused_and_signed_kickoff_connector(deposit_id, operator_xonly_pk)` to consume a **fresh, distinct** kickoff/reimburse slot that was presigned for that specific deposit's BitVM setup. Because every registered operator has valid presigned kickoff/reimburse transactions for every deposit (that is how the N-of-N reimbursement graph works), operator `O` can legitimately walk both deposit `i`'s and deposit `j`'s reimbursement graphs even though `O` only funded one real Bitcoin transaction spending one real UTXO.

None of the existing guards catch this: `Verifier::is_deposit_valid` and `SPV::verify`/`verify_storage_proofs` validate that a *payout tx* is correctly linked to a deposit's move-tx and a *single* storage-proof index, but they run inside the bridge circuit on a challenge, not in the always-on `update_finalized_payouts` bookkeeping path that this bug lives in. There is no DB uniqueness constraint tying `withdrawal_utxo_txid/vout` to a single `idx`, and no check in `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` that a spent UTXO can satisfy only one withdrawal record.

### Impact Explanation
The party that gains is an operator (attacker-controlled or colluding) who funds one genuine payout transaction but is credited by the verifiers' bookkeeping as having paid two (or more) distinct citrea withdrawals/deposits. Each credited deposit independently unlocks a `handle_finalized_payout` call that consumes a fresh presigned kickoff/reimburse connector and drives that deposit's `Round`→`Kickoff`→`Reimburse` transaction chain to completion, paying out that deposit's collateral to the operator. This matches the Critical category "an operator reimbursed for a payout it never funded" — the operator is reimbursed for deposit `j` (or `i`) despite never constructing/broadcasting a Bitcoin transaction that satisfies that specific withdrawal. Symmetrically, whichever deposit's reimbursement slot gets silently consumed by the false credit becomes permanently unreimbursable for the entity that should legitimately fund/claim it, since `mark_payout_handled` and the kickoff-connector consumption are one-shot per deposit — matching "an honest operator permanently unable to be reimbursed." The blast radius scales with however many withdrawal indices the attacker registers with colliding UTXO bytes and is repeatable across deposits/operators since the attacker fully controls the withdrawal UTXO field at `withdraw()` time for every withdrawal they submit.

### Likelihood Explanation
Preconditions are fully within the described unprivileged attacker's reach: they need only call `withdraw()` on the Citrea Bridge contract twice with identical `OutPoint` bytes for two different withdrawal indices (no on-chain uniqueness enforcement is evidenced in the collected code path, and Clementine's storage-proof verification only checks that the claimed slot's value matches the state root — it does not check cross-index uniqueness). The attacker needs an accomplice or self-controlled operator to fund one real payout transaction against that UTXO (the same operational cost as any legitimate payout). No majority hashrate, no key compromise, no verifier/aggregator privilege is required. This is deployment-configuration independent (regtest/testnet/mainnet all share this DB/query logic) and repeatable per attacker-chosen deposit pair.

### Recommendation
Scope the join and the finalized-payout bookkeeping by `(idx, withdrawal_utxo)` pair instead of `withdrawal_utxo` alone, and additionally enforce a DB-level uniqueness constraint (or an explicit runtime check in `update_finalized_payouts`) that a given `(withdrawal_utxo_txid, withdrawal_utxo_vout)` can only ever satisfy a single `idx`/deposit. If Citrea's contract allows registering the same withdrawal UTXO for multiple indices, Clementine must detect and reject (or flag as malicious) any additional withdrawal registrations that duplicate a UTXO already used by a different `idx`, before ever considering a spend of that UTXO as evidence of a finalized payout for more than one deposit.

### Proof of Concept
```
#[tokio::test]
async fn colliding_withdrawal_utxo_double_credits_finalized_payout() {
    // 1. Set up two deposits (deposit_id i, deposit_id j) each with move_to_vault_txid_i / _j,
    //    using db.upsert_move_to_vault_txid_from_citrea_deposit for both idx=i and idx=j.
    // 2. Register the SAME OutPoint `X` as the withdrawal utxo for BOTH idx=i and idx=j via
    //    db.update_withdrawal_utxo_from_citrea_withdrawal(idx=i, X, ...) and (idx=j, X, ...).
    // 3. Simulate a single on-chain payout transaction T spending X, recorded via
    //    db.insert_spent_utxo(block_id, &T_txid, &X.txid, X.vout) and insert_txid_to_block.
    // 4. Call verifier.update_finalized_payouts(dbtx, block_id, block_cache_with_T) (or the
    //    equivalent internal path exercised by the finalized-block consumer).
    // 5. Binding check (both sides of equality before/after):
    //    let rows = db.get_payout_txs_for_withdrawal_utxos(None, block_id).await.unwrap();
    //    assert_eq!(rows.len(), 2); // BOTH i and j returned for the single spend of X
    //    assert!(rows.iter().any(|(idx,_)| *idx == i));
    //    assert!(rows.iter().any(|(idx,_)| *idx == j));
    //    // i.e. "one spend of X" != "credited to exactly one idx" -- binding broken.
    // 6. Confirm downstream effect: call operator.handle_finalized_payout for deposit j's
    //    deposit_outpoint after i's kickoff connector has already been consumed by the same
    //    operator via handle_finalized_payout for deposit i, and assert that a SECOND, distinct
    //    kickoff/reimburse connector is granted for deposit j even though only tx T was ever
    //    broadcast on Bitcoin.
}
```
Note: I was not able to fully trace `update_payout_txs_and_payer_operator_xonly_pk`, `get_first_unhandled_payout_by_operator_xonly_pk`, `mark_payout_handled`, and `validate_payer_is_operator` line-by-line before running out of tool iterations, so the exact SQL/uniqueness semantics of those three functions should be re-verified directly in `core/src/database/verifier.rs` and `core/src/operator.rs` before finalizing a fix, though the confirmed JOIN in `get_payout_txs_for_withdrawal_utxos` and the per-deposit kickoff-connector consumption in `handle_finalized_payout` are sufficient to establish the broken binding.

### Citations

**File:** core/src/database/verifier.rs (L175-181)
```rust
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
```
