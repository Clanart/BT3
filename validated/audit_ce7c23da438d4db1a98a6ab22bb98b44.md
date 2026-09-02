### Title
`update_finalized_payouts` keys payouts by withdrawal `OutPoint` alone, letting one payout transaction credit two different deposits — ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` derives, per Bitcoin block, the set of `(withdrawal idx, payout_txid, operator_xonly_pk)` tuples purely from a SQL join keyed on the withdrawal UTXO's `(txid, vout)`, with no `deposits.deposit_id`/`withdrawals.idx` uniqueness check on that pair. If two `withdrawals` rows (for two different deposits) are ever populated with the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, a single real Bitcoin spend of that UTXO gets attributed to *both* rows, crediting the same operator/payout data to a deposit it never actually paid out.

### Finding Description
The binding that must hold is: **for withdrawal index `i`, the operator recorded in `withdrawals.payout_payer_operator_xonly_pk` for `i` == the operator whose Bitcoin transaction actually paid out `i`'s specific withdrawal UTXO, and this attribution happens exactly once per real payout.**

The lookup that feeds `update_finalized_payouts` is: [1](#0-0) 
This joins `bitcoin_syncer_spent_utxos` to `withdrawals` solely on `(txid, vout)` — not on `deposit_id`/`idx` uniqueness. The `withdrawals` table itself has no `UNIQUE` constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`: [2](#0-1) 

`update_finalized_payouts` then iterates the query result and, for every `(idx, payout_txid)` pair, parses the OP_RETURN of that one transaction to derive `operator_xonly_pk`, `block_hash`, pushing a tuple per `idx`: [3](#0-2) 
and writes them all via `update_payout_txs_and_payer_operator_xonly_pk`: [4](#0-3) 

If two `withdrawals` rows for deposits A and B end up with identical `withdrawal_utxo_txid`/`withdrawal_utxo_vout` values (populated per-idx via `update_withdrawal_utxo_from_citrea_withdrawal`, driven by data the withdrawer supplies on the Citrea side): [5](#0-4) 
then the single real on-chain transaction that spends that UTXO satisfies the join for *both* rows, and `update_finalized_payouts` writes the same `payout_txid`/`operator_xonly_pk`/`payout_tx_blockhash` into both A's and B's rows. Downstream, `PayoutCheckerTask::run_once` picks up "unhandled payouts" per operator xonly key and, independently for each row, calls `Operator::handle_finalized_payout` and later `mark_payout_handled`: [6](#0-5) 
Because the two rows are independent DB records (`idx` primary key), both A and B are processed as fulfilled by the *same* operator payout, even though only one of the two withdrawal recipients was actually paid by that transaction.

None of the checks the audit rules point to (`Verifier::is_deposit_valid`, `SPV::verify`, storage-proof verification, etc.) close this gap: they validate that a *given* deposit's registered withdrawal UTXO matches what the operator/aggregator is signing against (e.g. `sign_optimistic_payout`'s `withdrawal_utxo != input_outpoint` check at core/src/verifier.rs:1646-1659), but none of them enforce that the `(txid, vout)` value stored per `idx` is unique across the whole `withdrawals` table, and the reconciliation query that actually attributes real Bitcoin payouts to deposits (`get_payout_txs_for_withdrawal_utxos`) does not include `deposit_id`/`idx` in its uniqueness reasoning at all.

### Impact Explanation
An operator can be credited/reimbursed for a payout tied to a deposit it never funded, or the deposit that was genuinely paid can have its reimbursement record silently overwritten/duplicated in a way that lets the *wrong* deposit consume the finalized-payout attribution — this is the Critical category "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed." The blast radius scales with however many withdrawal indices end up sharing an outpoint value in the `withdrawals` table, which is not bounded by any schema or application-level constraint in this repository.

### Likelihood Explanation
This requires that the `withdrawals` table (populated from Citrea withdrawal data) end up with two rows sharing an identical `(withdrawal_utxo_txid, withdrawal_utxo_vout)`. This repository's own schema and code path place no constraint preventing that, and the reconciliation query is naively keyed on the UTXO alone rather than `(idx, UTXO)`. Whether this specific collision can actually be produced end-to-end depends on whether the Citrea Bridge contract's `withdraw` function (out of this repo) allows two distinct withdrawal requests to register the same target outpoint — the audit question asserts this is possible ("attacker-chosen bytes... with no uniqueness enforcement"), but I could not locate the Citrea Bridge contract source in this repository to confirm or refute that precondition; the contract logic is out-of-scope infrastructure this repo trusts via light-client proofs.

### Recommendation
Change `get_payout_txs_for_withdrawal_utxos` (and the update it feeds) to key strictly per `withdrawals.idx`, and add a uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table (or reject/flag a second withdrawal registration that reuses an outpoint already bound to another `idx`) so `update_finalized_payouts` cannot attribute one real spend to more than one deposit.

### Proof of Concept
```rust
// core/src/database/verifier.rs (or a new test module)
#[tokio::test]
async fn duplicate_withdrawal_utxo_double_attributes_payout() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = OutPoint { txid: Txid::from_byte_array([0x99; 32]), vout: 0 };
    let payout_txid = Txid::from_byte_array([0x11; 32]);

    // idx_a and idx_b are two distinct deposits/withdrawals
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 1, &Txid::from_byte_array([0xAA;32])).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 2, &Txid::from_byte_array([0xBB;32])).await.unwrap();

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    // BUG: both idx 1 and idx 2 registered with the SAME withdrawal_utxo
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 1, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 2, shared_utxo, block_id).await.unwrap();

    let txs = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // Assert the flaw: the single real payout_txid is attributed to BOTH idx 1 and idx 2,
    // i.e. attribution is not exactly-once per real payout.
    assert_eq!(txs.len(), 2);
    assert!(txs.iter().any(|(idx, txid)| *idx == 1 && *txid == payout_txid));
    assert!(txs.iter().any(|(idx, txid)| *idx == 2 && *txid == payout_txid));
}
```
This demonstrates that `get_payout_txs_for_withdrawal_utxos` (and thus `update_finalized_payouts`) does not enforce `deposit_id`/`idx` uniqueness against the withdrawal UTXO, breaking the exactly-once attribution binding.

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

**File:** core/src/verifier.rs (L2296-2343)
```rust
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
```

**File:** core/src/verifier.rs (L2345-2350)
```rust
        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L39-106)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
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
