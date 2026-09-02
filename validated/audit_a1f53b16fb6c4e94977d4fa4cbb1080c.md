### Title
Withdrawal-UTXO-only join in `get_payout_txs_for_withdrawal_utxos` lets one payout tx satisfy two withdrawal indices, letting an operator be reimbursed twice for a single fronted payout - ([File: core/src/database/verifier.rs])

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) matches a spent UTXO to a withdrawal purely by `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, with no `idx`/deposit binding and no uniqueness constraint on that column pair in the `withdrawals` table (core/src/database/schema.sql:269-281). If Citrea ever assigns the same UTXO bytes to two different withdrawal indices, one physical Bitcoin spend of that UTXO makes `update_finalized_payouts` (core/src/verifier.rs:2283-2353) credit **both** indices with the same `payout_txid`/`payer_operator_xonly_pk`, and `PayoutCheckerTask` (core/src/task/payout_checker.rs:31-111) will subsequently run `handle_finalized_payout` for both, reimbursing the operator from two separate deposit vaults for a payout it only funded once.

### Finding Description
The intended binding is: **exactly one on-chain payout transaction spending a withdrawal UTXO should credit exactly one withdrawal index** (the index whose UTXO was actually spent, verified against that specific deposit). Instead the code enforces only:
`bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout` (core/src/database/verifier.rs:176-181), with no `idx`/deposit disambiguation and no DB uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` (core/src/database/schema.sql:269-281).

`withdrawal_utxo_txid`/`vout` for index N is populated verbatim from Citrea's `withdrawalUTXOs(N)` via `collect_withdrawal_utxos`/`update_withdrawal_utxo_from_citrea_withdrawal` (core/src/citrea.rs:458-496, core/src/database/verifier.rs:108-135). The attacker owns a small BTC UTXO `U` and calls the Citrea Bridge contract's `withdraw(U.txid, U.vout)` (core/src/test/withdraw.rs:133-138) twice, obtaining two distinct, sequentially-assigned withdrawal indices `i` and `j` that both store `U` as their `withdrawal_utxo`. Nothing in Clementine (or, per the described threat model, in the Citrea contract) rejects reusing `U` across indices.

When the operator's `withdraw()` gRPC handler (core/src/operator.rs:560-627) is called once for index `i` with `in_outpoint = U`, it fronts a single payout transaction `PT` that spends `U`. The block syncer records this single spend in `bitcoin_syncer_spent_utxos`. `get_payout_txs_for_withdrawal_utxos` then joins this one spent-UTXO row against **both** withdrawal rows `i` and `j` (since both have `withdrawal_utxo = U`), returning `[(i, PT), (j, PT)]`. `update_finalized_payouts` accepts both and calls `update_payout_txs_and_payer_operator_xonly_pk` (core/src/database/verifier.rs:198-251), writing `payout_txid = PT` and the operator's xonly pubkey onto **both** rows.

`get_first_unhandled_payout_by_operator_xonly_pk` (core/src/database/verifier.rs:282-313) and `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:39-111) then process each unhandled row independently, calling `Operator::handle_finalized_payout` (core/src/operator.rs:839-885) once per index — once for deposit `i`'s vault (legitimately) and once for deposit `j`'s vault (illegitimately), each producing a kickoff/reimbursement claim, even though the operator only ever funded one payout transaction. The on-chain bridge circuit's `verify_storage_proofs` (circuits-lib/src/bridge_circuit/storage_proof.rs:44-133) and `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs:137-236) do not catch this either: they only check that the specific `storage_proof.index` used for kickoff `j`'s claim maps to `U` and that `PT` spends `U` at `payout_input_index` — both true, since `U` really was assigned to index `j` on Citrea and really was spent by `PT`. There is no check that `PT` was not already claimed by another index's kickoff.

### Impact Explanation
An operator can be reimbursed twice — once correctly for the payout it fronted at index `i`, and once for index `j`'s deposit vault which it never funded a payout for. This is bridge value (a full `move-to-vault` UTXO) leaving the vault for deposit `j` without a matching fronted payout — an operator reimbursed for a payout it never funded. This matches the Critical impact category directly. The attack is repeatable for any two withdrawal indices that end up sharing an assigned UTXO, and the blast radius scales with the number of deposits/withdrawals in flight and is not limited to one operator.

### Likelihood Explanation
The attack requires only: (1) owning one small BTC UTXO `U` (attacker-controlled, no verifier/operator/aggregator privilege needed), (2) calling the public `withdraw()` function on Citrea's Bridge contract twice with the same `(U.txid, U.vout)` argument to obtain two withdrawal indices referencing `U`, and (3) invoking the operator's public `withdraw()` gRPC once for one of those indices to get a real payout tx spending `U` broadcast. No verifier, watchtower, or operator collusion, key compromise, or majority hashrate is required. The main precondition — that Citrea's contract does not deduplicate `withdraw()` UTXO arguments across indices — is stated as given in the threat model and is not contradicted by anything visible in this repository's Citrea client integration code, which simply reads back whatever `withdrawalUTXOs(idx)` returns per index with no cross-index dedup check on Clementine's side either.

### Recommendation
Change `get_payout_txs_for_withdrawal_utxos`'s join (and the underlying data model) to bind a spent-UTXO row to at most one withdrawal `idx`: e.g. add a uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table (rejecting/flagging duplicate assignments when ingesting Citrea withdrawal data), and/or have `update_finalized_payouts`/`update_payout_txs_and_payer_operator_xonly_pk` only credit a single, deterministically-chosen `idx` per spent UTXO (e.g., the lowest unclaimed idx) with an explicit check that a `payout_txid` is not attributed to more than one `idx` overall.

### Proof of Concept
```rust
// core/src/database/verifier.rs (test module)
#[tokio::test]
async fn duplicate_withdrawal_utxo_causes_double_credit() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0x11; 32]),
        vout: 0,
    };
    let spending_txid = Txid::from_byte_array([0x22; 32]);
    let idx_i = 10u32;
    let idx_j = 11u32;

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &spending_txid).await.unwrap();

    // Two different withdrawal indices assigned the SAME withdrawal_utxo, as could
    // result from calling Citrea's withdraw(txid, vout) twice with identical args.
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_i, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_j, shared_utxo, block_id).await.unwrap();

    // The shared UTXO is spent exactly ONCE on-chain.
    db.insert_spent_utxo(&mut dbtx, block_id, &spending_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    let results = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // BINDING: one spent vault UTXO for withdrawal i == one settled payout claim.
    // BROKEN: query returns 2 rows (idx_i and idx_j) for a single spend.
    assert_eq!(results.len(), 1, "one physical spend must not credit two withdrawal indices");
}
```
Run with `cargo test -p clementine-core duplicate_withdrawal_utxo_causes_double_credit --features integration-tests` against a local test Postgres instance (no mainnet, no live Citrea required). The test is expected to fail today with `results.len() == 2`, demonstrating that `update_finalized_payouts`/`update_payout_txs_and_payer_operator_xonly_pk` would mark both withdrawal rows paid from a single fronted payout.