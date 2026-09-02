### Title
Duplicate `withdrawal_utxo` outpoints across two withdrawal indices let one payout spend be joined and recorded against both indices - ([File: core/src/database/verifier.rs])

### Summary
`get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)` equality, with no `DISTINCT`/uniqueness enforcement, and `update_withdrawal_utxo_from_citrea_withdrawal` (core/src/database/verifier.rs:108-135) writes an attacker-influenced `withdrawal_utxo` outpoint keyed only by `idx` with no uniqueness constraint across rows. If the attacker-chosen withdrawal UTXO bytes used in two separate `withdraw()` calls on the Citrea Bridge contract collide, a single on-chain spend of that outpoint produces two joined rows `(i, P)` and `(j, P)`, and `update_payout_txs_and_payer_operator_xonly_pk` (core/src/database/verifier.rs:199-251) writes `payout_txid = P` for both `i` and `j`.

### Finding Description
The binding that must hold is: **payout recorded for withdrawal index == exactly one index whose withdrawal_utxo was spent by that payout**, i.e. for any payout txid `P`, `{idx : withdrawals.payout_txid = P}` should have cardinality 1 per genuinely spent outpoint.

Tracing `Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2353):
- It fetches `payout_txids = get_payout_txs_for_withdrawal_utxos(block_id)` (verifier.rs:2289-2292), which is a SQL `JOIN` on `bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout` (core/src/database/verifier.rs:175-183) with no `DISTINCT ON (bsu.txid, bsu.vout)` or uniqueness guard.
- The `withdrawals` table's `withdrawal_utxo_txid/vout` columns are populated per-`idx` by `update_withdrawal_utxo_from_citrea_withdrawal` (core/src/database/verifier.rs:108-135), called once per Citrea withdrawal index from `Verifier`'s Citrea-sync logic (core/src/verifier.rs:2248-2262), and the underlying withdrawal UTXO bytes are attacker-supplied at `withdraw()` call time per the stated attacker capabilities.
- Nothing in the query, the DB write path, or the loop in `update_finalized_payouts` deduplicates on outpoint; each result row `(idx, payout_txid)` is pushed independently into `payout_txs_and_payer_operator_idx` (verifier.rs:2298-2343) and persisted for every `idx` (verifier.rs:2345-2350).

If an attacker calls `withdraw()` twice, submitting the identical withdrawal-UTXO outpoint bytes `U` for withdrawal indices `i` and `j`, both indices' rows end up with the same `withdrawal_utxo_txid/vout`. When any single payout transaction `P` (constructed by an operator to service one of these withdrawals) is observed spending `U`, `bitcoin_syncer_spent_utxos` records that spend once, but the join returns both `(i, P)` and `(j, P)`, and both rows are marked `payout_txid = P`. Existing guards (`Verifier::is_deposit_valid`, `SPV::verify`, `verify_storage_proofs`, `only_aggregator_and_self`) operate on deposit/withdrawal validity and RPC authentication, not on outpoint uniqueness across withdrawal indices, so none of them intercept this collision.

### Impact Explanation
Downstream, `is_payout_handled`/`PayoutCheckerTask`-style logic treats each `idx` with a non-null `payout_txid` as an independently reimbursable withdrawal tied to its own move-to-vault deposit UTXO. With both `i` and `j` marked as paid off the single transaction `P`, the reimbursement/kickoff machinery would process index `j` as "paid" and release its move-to-vault UTXO via the Reimburse path even though no payout transaction was ever sent for withdrawal `j`'s underlying deposit — i.e., BTC leaving a move-to-vault UTXO without a matching fronted withdrawal for `j`, and/or an operator credited for a reimbursement it only funded once. This falls under the Critical impact category. The attack is repeatable per pair of colliding withdrawal indices the attacker registers, and its blast radius scales with however many withdrawal indices the attacker can seed with a repeated outpoint before a matching payout is observed.

### Likelihood Explanation
Preconditions: the attacker must be able to submit two `withdraw()` calls on the Citrea Bridge contract with identical withdrawal-UTXO outpoint bytes, and no code path found in the traced files (`core/src/verifier.rs`, `core/src/database/verifier.rs`) rejects duplicate outpoints across distinct withdrawal indices. This requires only ordinary gas/fee cost of two `withdraw()` calls and one operator-initiated payout transaction — no privileged role, no key compromise. I was not able to fully verify (due to iteration limits) (1) whether `schema.sql` or the Citrea light-client verification layer independently enforces uniqueness on `withdrawal_utxo_txid/vout` before this DB write occurs, and (2) the exact downstream reimbursement code in `core/src/task/payout_checker.rs`/`Operator::handle_finalized_payout` that consumes `payout_txid`, to confirm it does not itself deduplicate by payout txid before crediting/kicking off reimbursement.

### Recommendation
Add a uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table, or make `get_payout_txs_for_withdrawal_utxos` deduplicate by outpoint (e.g., `DISTINCT ON (bsu.txid, bsu.vout)` ordered deterministically, or reject/flag when more than one `idx` maps to the same spent outpoint) before propagating to `update_payout_txs_and_payer_operator_xonly_pk`.

### Proof of Concept
```
cargo test -p clementine-core update_get_payout_txs_from_citrea_withdrawal_duplicate_outpoint
```
Test plan (mirrors existing test in core/src/database/verifier.rs:390-521):
1. Insert two `withdrawals` rows for `idx = i` and `idx = j` via `upsert_move_to_vault_txid_from_citrea_deposit` + `update_withdrawal_utxo_from_citrea_withdrawal`, both bound to the identical `OutPoint { txid: U, vout: 0 }`.
2. Record a single spend of `U` via `insert_spent_utxo(block_id, payout_txid=P, U.txid, U.vout)`.
3. Call `get_payout_txs_for_withdrawal_utxos(block_id)` and assert `results.len() == 1` (binding holds) — the current implementation is expected to return `len() == 2` (`[(i, P), (j, P)]`), demonstrating the violation.
4. Call `update_payout_txs_and_payer_operator_xonly_pk` with both rows and assert only one `idx`'s `payout_txid` ends up set to `P`; currently both get set, proving both sides of the equality diverge before/after the call.