### Title
Reorg-driven reprocessing overwrites `payout_payer_operator_xonly_pk` for an already-handled withdrawal with no `is_payout_handled` guard - (File: core/src/database/verifier.rs)

### Summary
`update_payout_txs_and_payer_operator_xonly_pk` performs an unconditional `UPDATE withdrawals ... WHERE w.idx = c.idx` with no check on `is_payout_handled`, so if `bitcoin_syncer` reprocesses a reorged chain where the payout tx at a given block height/idx has changed (different OP_RETURN / different funder), the row's `payout_payer_operator_xonly_pk` and `payout_tx_blockhash` will be silently overwritten even though `mark_payout_handled` and `end_round` already ran for the pre-reorg operator.

### Finding Description
The binding that should hold is: `payout_payer_operator_xonly_pk` stored in `withdrawals` for a given `idx` == the xonly pubkey of whichever operator actually funded `output0` value in the currently-canonical payout transaction, **and** once `is_payout_handled = TRUE` this attribution must never change again, because collateral release (`end_round`) has already been triggered for the attributed operator.

`update_payout_txs_and_payer_operator_xonly_pk` (core/src/database/verifier.rs:198-251) builds a plain `UPDATE ... FROM (VALUES ...) AS c WHERE w.idx = c.idx` [1](#0-0)  with no predicate on `is_payout_handled`. If the verifier's block-processing pipeline (`Verifier::update_finalized_payouts`, which calls into this function) is invoked again after a Bitcoin reorg swaps the block at the same height for one containing an attacker-rewritten payout tx (same input0/output0, different OP_RETURN encoding a different `operator_xonly_pk`), the row is overwritten regardless of the fact that `mark_payout_handled` (core/src/task/payout_checker.rs:104-106) already fired for the pre-reorg attribution and `end_round`/collateral-release logic already executed for that operator.

`PayoutCheckerTask::run_once` only reads unhandled payouts filtered by `is_payout_handled = FALSE` per `get_first_unhandled_payout_by_operator_xonly_pk` (core/src/database/verifier.rs:282-300), and once handled it never revisits the row. So the write path that mutates attribution (`update_payout_txs_and_payer_operator_xonly_pk`) and the read/consume path that releases collateral (`get_first_unhandled_payout_by_operator_xonly_pk` + `mark_payout_handled`) are not coordinated by any guard preventing a later reorg-driven write from stomping an already-consumed attribution.

### Impact Explanation
This falls under the Critical category "an honest operator permanently unable to be reimbursed" / "an operator reimbursed for a payout it never funded": whichever operator actually funds `output0` in the final canonical chain has their attribution overwritten in the DB, but the pre-reorg operator already had `end_round`/collateral release triggered and is marked handled — they cannot be re-processed since the unhandled-payout query filters on `is_payout_handled = FALSE`. This is repeatable per withdrawal/operator whenever a reorg at the exact settlement height replaces the payout transaction.

### Likelihood Explanation
I was unable to fully trace `Verifier::update_finalized_payouts` (the caller in core/src/verifier.rs) or the `bitcoin_syncer` reorg-unwind/rescan logic within the available tool budget, so I cannot confirm with certainty whether an upstream guard (e.g., re-validation against `is_payout_handled` before calling the update, or a check that the reorg cannot touch already-finalized-depth blocks) exists at the call site that would prevent this write from ever being reached for a handled row. The concrete evidence obtained — the `UPDATE` statement itself lacking any `is_payout_handled` predicate — is a real gap in this specific function, but confirming end-to-end exploitability requires inspecting `Verifier::update_finalized_payouts` and the syncer's reorg handling, which I could not complete.

### Recommendation
Add `AND w.is_payout_handled = FALSE` to the `UPDATE` in `update_payout_txs_and_payer_operator_xonly_pk`, and/or have the reorg-handling path in `bitcoin_syncer`/`update_finalized_payouts` refuse to reprocess or re-attribute payouts for withdrawals whose `is_payout_handled` is already `TRUE`, instead flagging a critical alert for manual/protocol-level reconciliation.

### Proof of Concept
Could not be fully constructed/verified within the available investigation budget — a regtest test would need to: (1) confirm an honest payout tx, run `PayoutCheckerTask::run_once` to set `is_payout_handled = TRUE` and `payout_payer_operator_xonly_pk = honest_pk`; (2) `invalidateblock` at that height and mine an attacker-rewritten payout tx (same input0/output0, different OP_RETURN operator pk) then `reconsiderblock`; (3) trigger the verifier's block-reprocessing path and assert whether `payout_payer_operator_xonly_pk` changes despite `is_payout_handled` already being `TRUE`. This last step (confirming the reprocessing call site actually re-invokes `update_payout_txs_and_payer_operator_xonly_pk` for already-handled rows) was not verified against `Verifier::update_finalized_payouts`/`bitcoin_syncer` source, so the finding above should be treated as a partially-confirmed code-level gap rather than a fully proven end-to-end exploit.

### Citations

**File:** core/src/database/verifier.rs (L226-248)
```rust
        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;
```
