### Title
Cross-index UTXO reuse in `get_payout_txs_for_withdrawal_utxos` lets one payout satisfy two `withdrawals` rows - ([File: core/src/database/verifier.rs])

### Summary
`update_withdrawal_utxo_from_citrea_withdrawal` writes an attacker-supplied Bitcoin outpoint into the `withdrawals` row for a given Citrea index with a plain `UPDATE ... WHERE idx = $1`, with no check that the outpoint is not already used by another index. `get_payout_txs_for_withdrawal_utxos` then joins `bitcoin_syncer_spent_utxos` to `withdrawals` purely on `(txid, vout)`, so if two indices share the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, a single spend of that UTXO produces two joined rows and `update_payout_txs_and_payer_operator_xonly_pk` sets `payout_txid`/`payout_payer_operator_xonly_pk` on both.

### Finding Description
The binding that must hold is: for each Bitcoin UTXO `U`, `count(idx : payout_txid set from spending U) == 1`.

- `update_withdrawal_utxo_from_citrea_withdrawal` persists the withdrawal outpoint per `idx` with no uniqueness check against other rows: [1](#0-0) 
- `get_payout_txs_for_withdrawal_utxos` joins strictly on `(txid, vout)`, not on `idx`: [2](#0-1) 
- The result set is fed straight into `update_payout_txs_and_payer_operator_xonly_pk`, which performs a bulk `UPDATE ... FROM (VALUES ...)` keyed by `idx`, applying the same `payout_txid`/`payout_payer_operator_xonly_pk`/`payout_tx_blockhash` to every matched `idx`: [3](#0-2) 
- `get_first_unhandled_payout_by_operator_xonly_pk` later returns any row where `payout_txid IS NOT NULL AND payout_payer_operator_xonly_pk = $1`, independent of whether the underlying spend genuinely paid that specific withdrawal's recipient/amount: [4](#0-3) 

If two Citrea withdrawal indices `i` and `j` are registered with the identical Bitcoin outpoint (same `withdrawal_utxo_txid`/`vout`), a single on-chain transaction that spends that outpoint will be recorded once in `bitcoin_syncer_spent_utxos`, but the JOIN in `get_payout_txs_for_withdrawal_utxos` produces both `(i, spending_txid)` and `(j, spending_txid)`. The subsequent bulk update sets `payout_txid` for both rows to the same `spending_txid` and credits the same operator for both. Nothing in the reviewed code path re-validates, per `idx`, that the payout transaction's actual output script/amount matches the recipient/amount specifically committed to for that `idx` before the DB write occurs — the match is driven solely by which input UTXO was spent, not by which withdrawal request the payment output actually satisfies.

I could not fully trace `Verifier::update_finalized_payouts` in `core/src/verifier.rs` (the caller of these DB functions) within the tool budget, so I cannot confirm with certainty whether it applies an additional per-`idx` check on the payout transaction's output (recipient script/amount) before calling `update_payout_txs_and_payer_operator_xonly_pk`, nor whether there is any Citrea-side or repo-side check elsewhere that rejects a `withdraw` call whose `input_outpoint` duplicates an already-registered outpoint from a different index. This is a gap in my verification, not a claim that such a check exists.

### Impact Explanation
If the exploit path is real, a single fronted BTC payment by an honest operator would be counted as satisfying two Citrea withdrawal indices, and `get_first_unhandled_payout_by_operator_xonly_pk` would let that operator (or any operator who happens to match `payout_payer_operator_xonly_pk`) initiate two separate kickoff/reimbursement flows for one real payment — an operator reimbursed for a payout it never funded, which is a Critical-severity impact per the rubric. This is repeatable per pair of duplicated indices and is not tied to a single deposit.

### Likelihood Explanation
Exploitability hinges entirely on whether the attacker can actually get two Citrea `withdraw` calls (indices `i`, `j`) accepted with the exact same `input_outpoint`. That gating logic lives partly in the Citrea Bridge contract (out of scope for root-cause attribution) and possibly in this repo's withdrawal-registration/event-handling code, which I was not able to fully inspect before the iteration budget ran out. Given the uncertainty on this precondition and on whether `update_finalized_payouts` performs additional per-index output validation, I cannot assign a confident likelihood rating without further investigation of `core/src/verifier.rs`'s full `update_finalized_payouts` implementation and any Citrea-withdrawal ingestion code that populates `withdrawal_utxo_txid`/`vout`.

### Recommendation
Add a uniqueness constraint (or an explicit application-level check) on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table so no two `idx` rows can reference the same Bitcoin outpoint, and/or change the JOIN in `get_payout_txs_for_withdrawal_utxos` (and the downstream reimbursement logic) to independently validate that the spending transaction's output actually satisfies the specific recipient/amount committed to for each matched `idx` before setting `payout_txid`.

### Proof of Concept
```rust
// core/src/database/verifier.rs (test module)
#[tokio::test]
async fn duplicate_withdrawal_utxo_across_two_indices() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0x77; 32]),
        vout: 0,
    };
    let idx_i = 10u32;
    let idx_j = 20u32;
    let move_txid_i = Txid::from_byte_array([0x01; 32]);
    let move_txid_j = Txid::from_byte_array([0x02; 32]);
    let payout_txid = Txid::from_byte_array([0x99; 32]);
    let operator_pk = generate_random_xonly_pk();

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_i, &move_txid_i).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_j, &move_txid_j).await.unwrap();

    // Both indices register the SAME Bitcoin outpoint as their withdrawal UTXO.
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_i, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_j, shared_utxo, block_id).await.unwrap();

    // Only ONE spend of shared_utxo occurs on-chain.
    let matches = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // BINDING UNDER TEST: count(idx : payout_txid set from UTXO shared_utxo) must == 1.
    // If the vulnerability is real, matches.len() == 2 (both idx_i and idx_j joined), violating the binding.
    assert_eq!(matches.len(), 1, "single UTXO spend must map to exactly one withdrawal index");
}
```
Run with `cargo test -p core update::verifier::tests::duplicate_withdrawal_utxo_across_two_indices` (adjust module path). A failing assertion (`matches.len() == 2`) confirms the DB-layer join allows one payout to satisfy two withdrawal indices; further work is needed to confirm whether upstream code (Citrea withdrawal ingestion / `update_finalized_payouts`) actually permits registering the duplicate outpoint in the first place, which I was unable to verify within the available tool budget.

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

**File:** core/src/database/verifier.rs (L199-251)
```rust
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

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

        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
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

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```
