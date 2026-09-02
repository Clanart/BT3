### Title
Payout attribution keyed only by `(withdrawal_utxo_txid, withdrawal_utxo_vout)` lets a single physical payout satisfy two distinct Citrea withdrawal rows, causing double reimbursement - (File: core/src/database/verifier.rs)

### Summary
`get_payout_txs_for_withdrawal_utxos` (called from `update_finalized_payouts` in `core/src/verifier.rs`) joins the `withdrawals` table to `bitcoin_syncer_spent_utxos` solely on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, without constraining the join to a single `idx`. If two `withdrawals` rows (two distinct Citrea withdrawal indices, each tied to a distinct deposit's `move_to_vault_txid`) are populated with the same withdrawal-UTXO bytes, one real on-chain spend of that UTXO produces two joined rows, and `update_payout_txs_and_payer_operator_xonly_pk` marks *both* rows as paid by the same operator with the same `payout_txid`. The `PayoutCheckerTask` then independently drives `handle_finalized_payout` for both deposits, letting the operator claim kickoff reimbursement against a vault it never actually funded a payout for.

### Finding Description
Binding claimed: `count(finalized_payouts rows keyed by withdrawal_utxo) == count(vault UTXOs actually spent for that withdrawal) == 1`.

The relevant code: [1](#0-0) 

This query returns one `(idx, spending_txid)` tuple per **withdrawals row** whose `withdrawal_utxo_txid/vout` matches an entry in `bitcoin_syncer_spent_utxos` for the given block — it is not scoped to a single `idx`, and there is no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the schema: [2](#0-1) 

`update_finalized_payouts` consumes this list, resolves the on-chain payout transaction once per returned tuple, and pushes a `(idx, payout_txid, operator_xonly_pk, block_hash)` row into `update_payout_txs_and_payer_operator_xonly_pk`: [3](#0-2) 

That update function performs a bulk `UPDATE ... FROM (VALUES ...)` keyed by `idx`, so it will happily write the *same* `payout_txid`/`operator_xonly_pk` into two different `withdrawals` rows if both were present in the input list: [4](#0-3) 

Root cause: the `withdrawal_utxo_txid`/`withdrawal_utxo_vout` value stored per `idx` originates directly from Citrea's `withdrawalUTXOs` mapping, populated via `collect_withdrawal_utxos`, which simply records whatever `(txid, vout)` bytes were supplied to the `withdraw`/`safeWithdraw` call for that withdrawal index: [5](#0-4) [6](#0-5) 

Neither `update_withdrawal_utxo_from_citrea_withdrawal` nor `get_payout_txs_for_withdrawal_utxos` enforces that a given `(txid, vout)` pair maps to only one `idx`: [7](#0-6) 

Attacker flow: the attacker (an ordinary Citrea user) calls `withdraw()`/`safeWithdraw()` twice, providing the identical `(txid, vout)` bytes for two different withdrawal indices `i` (later fulfilled via `optimistic_payout`, spending `move_to_vault_i` + the shared UTXO) and `j` (later fulfilled via an operator's own-funded payout tx that is later reimbursed through a kickoff/Reimburse tx against `move_to_vault_j`). Only one of these two payout transactions can ever physically spend the shared UTXO (a UTXO can be spent only once), but because `update_finalized_payouts`'s join keys purely on the UTXO bytes, whichever payout transaction is mined gets attributed to *both* `idx=i` and `idx=j` rows in `withdrawals`.

Downstream, `PayoutCheckerTask::run_once` picks up `get_first_unhandled_payout_by_operator_xonly_pk`, which selects unhandled rows purely by `payout_payer_operator_xonly_pk = $1` and `payout_txid IS NOT NULL`, with no cross-check that the same physical `payout_txid` isn't already attributed to another `idx`: [8](#0-7) 

This causes `handle_finalized_payout` to run twice — once for deposit `i`'s outpoint and once for deposit `j`'s outpoint — each independently kicking off a reimbursement claim against its own `move_to_vault` UTXO, even though only one of the two withdrawals was actually paid out with matching funds. None of the existing guards (`Verifier::is_deposit_valid`, `Operator::is_profitable`, `SECP.verify_schnorr`, presigned tx graph checks) intervene here because they validate the payout *transaction construction* per single deposit, not the *bookkeeping join* that cross-attributes one real spend to two DB rows.

### Impact Explanation
The vault of deposit `i` (or `j`, whichever's real payout tx did **not** occur) is drained via a kickoff/Reimburse claim that the operator never legitimately earned — i.e., "an operator reimbursed for a payout it never funded" and "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal," both Critical-severity impacts per the rubric. This is repeatable across any pair of deposits/withdrawals for which the attacker can register a colliding UTXO, and does not require operator, verifier, or aggregator privilege — only the ability to call `withdraw()` on Citrea twice with the same UTXO bytes and to broadcast one real payout transaction on Bitcoin.

### Likelihood Explanation
This requires that Citrea's Bridge contract itself permit registering the identical `(txid, vout)` bytes for two distinct withdrawal indices (no cross-index uniqueness check is visible/verifiable from this repo, since the contract lives outside the repo and is explicitly out of scope to audit directly). Given that constraint holds — which the question's attacker model instructs to assume — exploitation costs only the fees for two Citrea transactions and one Bitcoin payout transaction; no majority hashrate, key compromise, or privileged role is needed, making this cheap and repeatable if the precondition is met on the Citrea side.

### Recommendation
Scope `get_payout_txs_for_withdrawal_utxos` (and any related payout-detection logic) to match on `idx` in addition to `(txid, vout)`, or better, enforce a uniqueness constraint (or an explicit rejection at ingestion time in `update_withdrawal_utxo_from_citrea_withdrawal`) preventing two different `idx` rows from ever sharing the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`. Additionally, `get_first_unhandled_payout_by_operator_xonly_pk` and `handle_finalized_payout` should verify that the `payout_txid` attributed to `idx` actually contains an input spending that specific deposit's `move_to_vault_txid` output before allowing a kickoff/reimbursement to proceed, rather than trusting the DB attribution alone.

### Proof of Concept
```rust
// core/src/database/verifier.rs (new #[tokio::test])
// 1. Insert two withdrawals rows idx=1 (move_to_vault_1) and idx=2 (move_to_vault_2)
//    via upsert_move_to_vault_txid_from_citrea_deposit.
// 2. Call update_withdrawal_utxo_from_citrea_withdrawal(idx=1, utxo=U, ...)
//    and update_withdrawal_utxo_from_citrea_withdrawal(idx=2, utxo=U, ...)
//    with the SAME OutPoint U for both.
// 3. Insert exactly one spent-utxo record for U (insert_spent_utxo) pointing at a
//    single real payout_txid, simulating one physical Bitcoin spend.
// 4. Call get_payout_txs_for_withdrawal_utxos(block_id) and assert:
//    assert_eq!(txs.len(), 1, "only one physical spend occurred for utxo U");
//    // Current behavior: txs.len() == 2, i.e. [(1, payout_txid), (2, payout_txid)]
// 5. Call update_payout_txs_and_payer_operator_xonly_pk with both tuples, then
//    assert both idx=1 and idx=2 rows now have the SAME payout_txid recorded —
//    demonstrating both compete to draw the same operator reimbursement from
//    two distinct vault UTXOs despite only one payout event, refuting the
//    binding count(payout rows) == count(vault UTXOs spent) == 1.
```

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

**File:** core/src/database/verifier.rs (L198-251)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
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

**File:** core/src/database/schema.sql (L269-281)
```sql
create table if not exists withdrawals (
    idx int primary key,
    move_to_vault_txid bytea not null,
    withdrawal_utxo_txid bytea,
    withdrawal_utxo_vout int,
    withdrawal_batch_proof_bitcoin_block_height int,
    payout_txid bytea,
    payout_payer_operator_xonly_pk text,
    payout_tx_blockhash text check (payout_tx_blockhash ~ '^[a-fA-F0-9]{64}'),
    is_payout_handled boolean not null default false,
    kickoff_txid bytea,
    created_at timestamp not null default now()
);
```

**File:** core/src/verifier.rs (L2283-2350)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();

        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
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

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/citrea.rs (L458-496)
```rust
    async fn collect_withdrawal_utxos(
        &self,
        last_withdrawal_idx: Option<u32>,
        to_height: u64,
    ) -> Result<Vec<(u64, OutPoint)>, BridgeError> {
        let mut utxos = vec![];

        let mut start_idx = match last_withdrawal_idx {
            Some(idx) => idx + 1,
            None => 0,
        };

        loop {
            let withdrawal_utxo = self
                .contract
                .withdrawalUTXOs(U256::from(start_idx))
                .block(BlockId::Number(BlockNumberOrTag::Number(to_height)))
                .call()
                .await;
            match withdrawal_utxo {
                Err(e) if e.to_string().contains("execution reverted") => {
                    tracing::trace!("Withdrawal utxo not found for index, error: {:?}", e);
                    break;
                }
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            let withdrawal_utxo = withdrawal_utxo.expect("Failed to get withdrawal UTXO");
            let txid = withdrawal_utxo.txId.0;
            let txid =
                Txid::from_slice(txid.as_ref()).wrap_err("Failed to convert txid to Txid")?;
            let vout = withdrawal_utxo.outputId.0;
            let vout = u32::from_le_bytes(vout);
            let utxo = OutPoint { txid, vout };
            utxos.push((start_idx as u64, utxo));
            start_idx += 1;
        }
        Ok(utxos)
    }
```

**File:** core/src/test/withdraw.rs (L133-144)
```rust
        let citrea_withdrawal_tx = citrea_client
            .contract
            .withdraw(
                FixedBytes::from(withdrawal_utxo.txid.to_raw_hash().to_byte_array()),
                FixedBytes::from(withdrawal_utxo.vout.to_le_bytes()),
            )
            .value(U256::from(
                config.protocol_paramset().bridge_amount.to_sat() * SATS_TO_WEI_MULTIPLIER,
            ))
            .send()
            .await
            .unwrap();
```
