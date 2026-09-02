### Title
Duplicate `withdrawal_utxo` across withdrawal indices lets one payout tx settle two withdrawal claims - ([File: core/src/database/verifier.rs])

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)` with no `LIMIT 1` or uniqueness enforcement on `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, and `update_withdrawal_utxo_from_citrea_withdrawal` never checks whether the outpoint being assigned to a given `idx` is already assigned to another `idx`. An attacker who owns two deposits can register the identical Bitcoin outpoint as the withdrawal UTXO for both deposit indices on Citrea, causing a single confirmed payout transaction to be attributed to both withdrawal rows.

### Finding Description
Binding claimed: `|{idx : get_payout_txs_for_withdrawal_utxos returns (idx, payout_txid) for outpoint O}| == 1`.

- `update_withdrawal_utxo_from_citrea_withdrawal` (`core/src/database/verifier.rs:108-135`) does an unconditional `UPDATE withdrawals SET withdrawal_utxo_txid=$2, withdrawal_utxo_vout=$3 WHERE idx=$1`, with no check that `(withdrawal_utxo_txid, withdrawal_utxo_vout)` is not already used by another row. This is called from `Verifier::update_citrea_deposit_and_withdrawals` (`core/src/verifier.rs:2248-2262`) for every withdrawal index returned by `CitreaClient::collect_withdrawal_utxos` (`core/src/citrea.rs:458-496`), which simply reads `withdrawalUTXOs(idx)` from the Citrea bridge contract for each successive index — values that the attacker (per the stated threat model) fully controls when calling Citrea's `withdraw`.
- `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) then does:
```
SELECT w.idx, bsu.spending_txid
FROM withdrawals w
JOIN bitcoin_syncer_spent_utxos bsu
   ON bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout
WHERE bsu.block_id = $1
```
If two `withdrawals` rows (idx1, idx2) carry the same `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, and the outpoint is spent exactly once on Bitcoin (it can only be spent once — Bitcoin doesn't allow double-spending a confirmed output), the JOIN fans out to both rows, returning `(idx1, payout_txid)` and `(idx2, payout_txid)` for the same physical spend.
- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) consumes this list and calls `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`), which sets `payout_txid`, `payout_payer_operator_xonly_pk`, `payout_tx_blockhash` on **both** `idx1` and `idx2` rows using the single physical payout tx's OP_RETURN-derived operator key.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) later picks up "the first unhandled payout" per operator key and calls `Operator::handle_finalized_payout` independently for each `idx`, since each deposit index has its own `move_to_vault_txid`/deposit outpoint. This drives two independent kickoff/reimbursement flows for two distinct deposits, both justified by the same single Bitcoin payout event.

None of the existing guards prevent this: `Operator::withdraw` (`core/src/operator.rs:588-596`) only checks that the outpoint supplied for a given `idx` matches what's stored for that `idx` — it never checks the outpoint isn't already claimed by a different `idx`. `is_deposit_valid`, `SPV::verify`, and the musig2/signature checks operate on deposit/kickoff data, not on withdrawal-UTXO uniqueness across indices. There is no DB constraint (unique index) on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in `withdrawals` (see `core/src/database/schema.sql`), and `bitcoin_syncer_spent_utxos`'s primary key is `(block_id, spending_txid, txid, vout)` (`core/src/database/schema.sql:114-120`), which does not prevent multiple `withdrawals` rows from referencing the same `(txid, vout)`.

### Impact Explanation
This lets an operator (automatically, via `PayoutCheckerTask`, without any operator misbehavior) be reimbursed twice — once per deposit index — for a single BTC payout output, meaning BTC leaves two move-to-vault UTXOs (for idx1 and idx2) while only one payout ever reached a withdrawing user. This matches the Critical category "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" / "operator reimbursed for a payout it never funded." The attack is repeatable across any pair of deposits the attacker controls and does not require operator collusion, only that the honest operator's own automation processes the finalized block.

### Likelihood Explanation
The attacker needs two prior deposits into the bridge (their own funds/withdraw rights) and the ability to call Citrea's `withdraw` twice with identical `input_outpoint` bytes for the two different deposit/withdrawal indices — both explicitly granted capabilities per the stated threat model. No verifier/operator/aggregator privilege, no majority hashrate, and no TLS/key compromise is required. The only cost is the two deposits' bridge amounts and normal transaction fees, both of which are otherwise legitimate flows.

### Recommendation
Enforce uniqueness of `(withdrawal_utxo_txid, withdrawal_utxo_vout)` across the `withdrawals` table (e.g., a unique index), and have `update_withdrawal_utxo_from_citrea_withdrawal` reject/flag an update that would set a withdrawal UTXO already used by a different `idx`. Additionally, make `get_payout_txs_for_withdrawal_utxos` defensive by deduplicating on the spent outpoint (e.g., grouping by `(bsu.txid, bsu.vout)` and erroring or picking only the first `idx` by insertion order) so a single spend can never be attributed to more than one withdrawal index.

### Proof of Concept
```rust
// core/src/database/verifier.rs (test module)
#[tokio::test]
async fn duplicate_withdrawal_utxo_yields_single_payout_claim() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let payout_txid = Txid::from_byte_array([0xAA; 32]);
    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0xBB; 32]),
        vout: 0,
    };

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    let idx1 = 1u32;
    let idx2 = 2u32;
    let move_txid1 = Txid::from_byte_array([0x01; 32]);
    let move_txid2 = Txid::from_byte_array([0x02; 32]);

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx1, &move_txid1).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx2, &move_txid2).await.unwrap();

    // Attacker registers the SAME outpoint for two different withdrawal indices.
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx1, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx2, shared_utxo, block_id).await.unwrap();

    // Single confirmed payout tx spending the outpoint.
    let results = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // Binding under test: exactly one idx should match outpoint O.
    assert_eq!(results.len(), 1, "one payout tx must not satisfy two withdrawal indices");
}
```
Running this against current code yields `results.len() == 2` (both `idx1` and `idx2` matched), demonstrating the broken binding.