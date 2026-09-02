### Title
Withdrawals table lacks uniqueness on `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, letting a single Bitcoin payout satisfy two withdrawal indices - (File: core/src/database/verifier.rs)

### Summary
`update_withdrawal_utxo_from_citrea_withdrawal` and `get_payout_txs_for_withdrawal_utxos` key/join strictly by `idx` without any uniqueness constraint on the withdrawal UTXO outpoint, and `schema.sql`'s `withdrawals` table has no `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)`. An attacker who is allowed to call Citrea's `withdraw` with arbitrary outpoint bytes can register the same withdrawal UTXO at two different withdrawal indices `i` and `j`; a single payout transaction spending that one UTXO then gets attributed to both indices, letting an operator claim reimbursement twice for one fronted payout.

### Finding Description
Binding claimed to hold: `COUNT(DISTINCT idx WHERE payout_txid = X AND that idx's withdrawal_utxo was spent by X) == 1` for any single payout transaction `X` spending one withdrawal UTXO. The code does not enforce this.

Trace:
- `update_withdrawal_utxo_from_citrea_withdrawal` (core/src/database/verifier.rs:108-135) writes `withdrawal_utxo_txid`/`vout` keyed solely by `idx`, with no check that this outpoint is already used by another `idx`.
- `schema.sql`'s `withdrawals` table has no unique constraint over `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, so two distinct `idx` rows can carry identical outpoint bytes.
- `update_citrea_deposit_and_withdrawals` (core/src/verifier.rs:2248-2262) calls this update once per `(idx, withdrawal_utxo_outpoint)` pair returned from `collect_withdrawal_utxos`, which is populated straight from Citrea's `withdrawalUTXOs(idx)` mapping (core/src/citrea.rs:458-496) — an index the attacker fully controls the content of via `withdraw`.
- `get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) joins `withdrawals w` to `bitcoin_syncer_spent_utxos bsu` on `bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout`. If two rows `w` (idx=i, idx=j) share the same outpoint, and there is exactly one `bsu` row for that outpoint (one payout tx spent it once), the join produces two result rows: `(i, txid)` and `(j, txid)`.
- `update_payout_txs_and_payer_operator_xonly_pk` (core/src/database/verifier.rs:199-251) then bulk-updates every `(idx, txid, operator_xonly_pk, blockhash)` tuple it is given, so both `idx=i` and `idx=j` get `payout_txid = txid` and the same `payout_payer_operator_xonly_pk`.
- Downstream, `get_first_unhandled_payout_by_operator_xonly_pk` (core/src/database/verifier.rs:282-313) and `mark_payout_handled` (core/src/database/verifier.rs:348-362) process unhandled payouts per `idx` independently — each `idx` corresponds to a distinct deposit/move-to-vault UTXO, so each is separately eligible to trigger `handle_finalized_payout`/kickoff/Reimburse for its own deposit.

No existing guard intercepts this: `is_deposit_valid`, `verify_storage_proofs`, and `SPV::verify` validate that a claimed outpoint/vout at a specific storage index matches Citrea's committed state for that index — they do not check that the outpoint is unique across indices. There is no DB uniqueness constraint and no application-level de-duplication anywhere in `update_citrea_deposit_and_withdrawals`, `get_payout_txs_for_withdrawal_utxos`, or `update_payout_txs_and_payer_operator_xonly_pk`.

Exploit flow: attacker calls Citrea's `withdraw`/`safeWithdraw` twice with identical `(txid, vout)` bytes for a self-controlled dust UTXO, producing two withdrawal indices `i` and `j` in Citrea's `withdrawalUTXOs` mapping that both resolve to the same Bitcoin outpoint. Clementine's block syncer records both. A cooperating (or even an honest, unaware) operator later builds one payout transaction spending that dust outpoint once (per `Operator::withdraw`, core/src/operator.rs:560-627). Once that payout is finalized on Bitcoin, `get_payout_txs_for_withdrawal_utxos` returns two rows for the one spend, and both `idx=i` and `idx=j` become "paid" by the operator, unlocking two separate Reimburse-eligible kickoffs for two distinct move-to-vault deposits from a single real payout.

### Impact Explanation
An operator (who may itself be the attacker's counterpart, or simply act on the state the attacker manufactured) can claim reimbursement for two deposits' move-to-vault UTXOs while having fronted funds for only one withdrawal payout — more BTC leaves move-to-vault UTXOs than was ever fronted to a withdrawer. This matches the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." It is repeatable per pair of deposits the attacker/operator can align, is not limited to a single operator, and scales with however many withdrawal indices the attacker registers against the same outpoint.

### Likelihood Explanation
The precondition is exactly the attacker capability enumerated in the threat model: an unprivileged party can call `withdraw` on the Citrea Bridge contract choosing arbitrary UTXO bytes, and can do so more than once. No verifier/operator/aggregator privilege, key share, or collateral is required from the attacker to create the duplicate registration; the actual double-reimbursement additionally needs a payout to be broadcast against that shared outpoint (normally performed by an operator during ordinary withdrawal servicing), so realizing full Critical impact requires an operator to service such a withdrawal, but the underlying data corruption (two `idx` rows sharing one outpoint, and the join/update logic conflating them) is fully attacker-triggerable and confirmable purely at the database layer with `MockCitreaClient`, independent of live Citrea or mainnet.

### Recommendation
Add a `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint (or a partial unique index excluding NULLs) on the `withdrawals` table, and reject/ignore new `withdrawal_utxo` registrations from Citrea whose outpoint already exists under a different `idx` in `update_withdrawal_utxo_from_citrea_withdrawal`/`update_citrea_deposit_and_withdrawals`. Additionally, harden `get_payout_txs_for_withdrawal_utxos` to defensively de-duplicate or error if more than one `idx` maps to the same spent outpoint within a block.

### Proof of Concept
```rust
// core/src/database/verifier.rs (test module) — cargo test
#[tokio::test]
async fn duplicate_withdrawal_utxo_across_two_idx_is_double_counted() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let move_txid_i = Txid::from_byte_array([0x10; 32]);
    let move_txid_j = Txid::from_byte_array([0x20; 32]);
    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0xAA; 32]),
        vout: 0,
    };
    let payout_txid = Txid::from_byte_array([0xBB; 32]);
    let block_id = db
        .insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0)
        .await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    // one physical spend of the shared outpoint
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into())
        .await.unwrap();

    let idx_i = 0u32;
    let idx_j = 1u32;
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_i, &move_txid_i).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_j, &move_txid_j).await.unwrap();

    // attacker-controlled Citrea `withdraw` calls register the SAME outpoint at two idx
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_i, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_j, shared_utxo, block_id).await.unwrap();

    // BINDING under test: one spend of one UTXO -> exactly one (idx, txid) pair
    let txs = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();
    assert_eq!(txs.len(), 1, "expected exactly one payout match for one physical spend, got {:?}", txs);
    // Demonstrates violation: current code returns 2, i.e. both idx_i and idx_j
}
```
Running this against the current implementation shows `txs.len() == 2` (both `idx_i` and `idx_j` matched), violating the binding and confirming that `update_payout_txs_and_payer_operator_xonly_pk` would mark both deposit indices as paid by the same single payout transaction.