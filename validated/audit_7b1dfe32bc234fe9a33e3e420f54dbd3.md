### Title
`update_withdrawal_utxo_from_citrea_withdrawal` performs no SQL-layer uniqueness check on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, allowing two `idx` rows to alias the same outpoint - ([File: core/src/database/verifier.rs])

### Summary
`Database::update_withdrawal_utxo_from_citrea_withdrawal` is a plain `UPDATE withdrawals SET withdrawal_utxo_txid=$2, withdrawal_utxo_vout=$3, ... WHERE idx=$1` with no `UNIQUE`/`ON CONFLICT`/`NOT EXISTS` guard on the outpoint columns. Two distinct Citrea withdrawal indices (`idx`) can therefore be assigned the identical `(withdrawal_utxo_txid, withdrawal_utxo_vout)` pair.

### Finding Description
The binding claimed by the code's design is: for all rows, `(withdrawal_utxo_txid, withdrawal_utxo_vout) → idx` is a function (at most one `idx` per outpoint). The implementation is: [1](#0-0) 

This `UPDATE` is keyed solely by `idx` (the primary key from the `withdrawals` table), and binds `withdrawal_utxo.txid`/`.vout` without any check that no other row already holds that pair. Since `withdrawal_utxo` originates from attacker-controlled bytes supplied to Citrea's `withdraw()` call (per the threat model, the attacker "chooses the bytes of a withdrawal UTXO"), an attacker can call `withdraw()` twice with identical outpoint bytes, producing two Citrea withdrawal indices `idx1 != idx2` that the verifier syncs into two separate `withdrawals` rows sharing the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`.

Downstream, `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)`: [2](#0-1) 

Because a real Bitcoin outpoint can be spent only once, a single spending transaction will match **both** aliased `idx` rows in this join, and the caller of this function (which then calls `update_payout_txs_and_payer_operator_xonly_pk`) would credit the same `payout_txid`/payer operator to both `idx1` and `idx2` rows, even though only one of them was actually funded by that spend.

I was not able to fully trace the caller in `core/src/verifier.rs` (event-sync loop) or confirm whether an additional application-level check (e.g., re-verification of storage proofs / deposit_id binding before insertion) filters duplicate outpoints before this DB write, nor could I retrieve the full `schema.sql` `withdrawals` table definition to conclusively confirm the absence of a `UNIQUE` index on these two columns. That downstream verification is required to establish whether "operator reimbursed for a payout it never funded" is actually reachable end-to-end, versus merely a data-model gap with no fund-movement consequence.

### Impact Explanation
If unmitigated elsewhere, an operator that paid a single payout aimed at `idx1`'s withdrawal could be recorded as having also paid `idx2`'s withdrawal (since both rows alias the same spent outpoint), potentially enabling that operator to claim reimbursement for a payout it never separately funded for `idx2` — matching the Critical impact category. This would be repeatable per pair of Citrea `withdraw()` calls the attacker can craft with colliding outpoints.

### Likelihood Explanation
Preconditions: attacker only needs to call Citrea's `withdraw()` twice with an identical withdrawal UTXO outpoint, well within the stated unprivileged capabilities. However, likelihood/severity depends entirely on unverified downstream logic (how `payer_operator_xonly_pk` is actually assigned to each `idx`, and whether the reimbursement/kickoff flow re-validates the outpoint against the specific `idx`/`deposit_id` via OP_RETURN or similar, per `verifier.rs` code I could not inspect within the available iterations).

### Recommendation
Add a `UNIQUE` constraint (or partial unique index, since `NULL` values must be allowed) on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table, and/or have `update_withdrawal_utxo_from_citrea_withdrawal` perform the update conditionally (e.g., `WHERE idx = $1 AND NOT EXISTS (SELECT 1 FROM withdrawals w2 WHERE w2.withdrawal_utxo_txid = $2 AND w2.withdrawal_utxo_vout = $3 AND w2.idx <> $1)`), returning an error on conflict so duplicate outpoints across different `idx` are rejected before being persisted.

### Proof of Concept
```rust
#[tokio::test]
async fn duplicate_withdrawal_utxo_across_idx_not_rejected() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0xAA; 32]),
        vout: 0,
    };
    let idx1 = 1u32;
    let idx2 = 2u32;

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx1, &bitcoin::Txid::from_byte_array([0x01;32])).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx2, &bitcoin::Txid::from_byte_array([0x02;32])).await.unwrap();

    // Same outpoint bound to two different idx: should be rejected if uniqueness were enforced.
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx1, utxo, 100).await.unwrap();
    let result2 = db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx2, utxo, 100).await;

    // Currently succeeds (no SQL-layer uniqueness), demonstrating the alias.
    assert!(result2.is_ok());

    let u1 = db.get_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx1).await.unwrap();
    let u2 = db.get_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx2).await.unwrap();
    assert_eq!(u1, u2); // both idx map to the identical outpoint
}
```

Note: this PoC demonstrates the row-level alias claimed by the question at the SQL layer within this repo's existing test harness (`create_test_config_with_thread_name`, no mainnet/live Citrea required). I could not, within the available exploration budget, produce a further test that traces this alias all the way through `payout_payer_operator_xonly_pk` assignment and a kickoff/reimbursement path to conclusively demonstrate actual fund misattribution — that requires reading the full `core/src/verifier.rs` sync/payout-matching logic and `schema.sql`'s complete `withdrawals` definition, which I was unable to retrieve before running out of tool-call iterations. This should be verified by a follow-up session with full file access before treating the Critical-impact claim as fully proven end-to-end.

### Citations

**File:** core/src/database/verifier.rs (L108-135)
```rust
    pub async fn update_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        withdrawal_utxo: OutPoint,
        withdrawal_batch_proof_bitcoin_block_height: u32,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET withdrawal_utxo_txid = $2,
                 withdrawal_utxo_vout = $3,
                 withdrawal_batch_proof_bitcoin_block_height = $4
             WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(withdrawal_utxo.txid))
        .bind(
            i32::try_from(withdrawal_utxo.vout)
                .wrap_err("Failed to convert withdrawal utxo vout to i32")?,
        )
        .bind(
            i32::try_from(withdrawal_batch_proof_bitcoin_block_height)
                .wrap_err("Failed to convert withdrawal batch proof bitcoin block height to i32")?,
        );

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L170-196)
```rust
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
    }
```
