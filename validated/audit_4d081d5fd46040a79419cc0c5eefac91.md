### Title
Missing uniqueness on `withdrawals.withdrawal_utxo_txid/vout` lets one spent UTXO satisfy multiple withdrawal indices, causing duplicate payout crediting and double reimbursement - (File: core/src/database/verifier.rs)

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)`, with no constraint that this pair is unique across `withdrawals` rows. If two different withdrawal indices end up with the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, a single Bitcoin payout transaction spending that UTXO is attributed to *both* indices, and `update_payout_txs_and_payer_operator_xonly_pk` writes the same `payout_txid`/operator/blockhash into both rows. This lets an operator's own automation (`PayoutCheckerTask`) later drain the second index's move-to-vault UTXO via `handle_finalized_payout`/`create_reimburse_txhandler`, even though no payout was ever made for that index.

### Finding Description
The binding that should hold is: for a withdrawal UTXO `U = (txid, vout)` actually spent on-chain, `count({ idx : withdrawals.idx joins bitcoin_syncer_spent_utxos on U }) == 1`.

- `get_payout_txs_for_withdrawal_utxos` [1](#0-0)  performs `SELECT w.idx, bsu.spending_txid FROM withdrawals w JOIN bitcoin_syncer_spent_utxos bsu ON bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout WHERE bsu.block_id = $1`. This returns one row per matching `withdrawals` row, not per spent UTXO.
- `withdrawals.idx` is the only primary key; there is no unique constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` [2](#0-1) .
- `update_withdrawal_utxo_from_citrea_withdrawal` blindly overwrites a row's `withdrawal_utxo_txid`/`vout` for whatever `citrea_idx` it is given, with no check against other rows [3](#0-2) . This is called once per (idx, OutPoint) pair pulled straight from Citrea's `collect_withdrawal_utxos` in the periodic sync loop [4](#0-3) .
- `update_finalized_payouts` consumes the join result as-is and calls `update_payout_txs_and_payer_operator_xonly_pk`, writing the *same* `payout_txid`/`operator_xonly_pk`/`block_hash` for every idx returned [5](#0-4) .
- `PayoutCheckerTask::run_once` then independently discovers each unhandled idx via `get_first_unhandled_payout_by_operator_xonly_pk` and, for each, calls `Operator::handle_finalized_payout` [6](#0-5) , which allocates a kickoff and eventually a `create_reimburse_txhandler` that spends that index's own `move_to_vault_txid`'s deposit output to the operator's reimbursement address [7](#0-6) .

Per the stated threat model, the attacker is explicitly granted the ability to "choose the bytes of a withdrawal UTXO" when calling `withdraw` on the Citrea Bridge contract. Using that capability, an attacker can register their own withdrawal index `j` (tied to their own deposit `move_to_vault_txid_j`) with `withdrawal_utxo` bytes identical to another, unrelated withdrawal index `i`'s UTXO (observable on-chain before or after `i` is paid). Nothing in this repository — not `get_payout_txs_for_withdrawal_utxos`, not `update_withdrawal_utxo_from_citrea_withdrawal`, not the `withdrawals` schema, not `update_finalized_payouts` — checks that a `withdrawal_utxo` is unique across `idx` rows. When any operator legitimately broadcasts a payout for `i`, the join matches both `i` and `j`, and `j` is marked as "paid" from a payout that never targeted it.

### Impact Explanation
The exploit lets index `j`'s row acquire a spurious `payout_txid`/`payout_payer_operator_xonly_pk`, causing `PayoutCheckerTask` to autonomously run `handle_finalized_payout` for deposit `j`, which drives the kickoff/reimburse transaction graph to eventually spend deposit `j`'s move-to-vault UTXO to the operator's reimbursement address — BTC leaving a move-to-vault UTXO with no matching fronted withdrawal for that deposit, matching the Critical category "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" / "an operator reimbursed for a payout it never funded." Because idx `j` must correspond to the attacker's own deposit (Citrea's deposit/withdrawal index correspondence), the direct financial loser is the attacker's own deposit, while an operator gains an unearned reimbursement; but the invariant break is real, reproducible, deterministic, and repeatable for any attacker-owned deposit/withdrawal pair colliding with any other withdrawal UTXO on the network.

### Likelihood Explanation
No privileged access is required: the attacker only needs to (1) make one bridge deposit, (2) call Citrea's `withdraw` for their own withdrawal slot with `withdrawal_utxo` bytes copied from another pending/confirmed withdrawal's UTXO, and (3) wait for any operator to broadcast a payout for that other withdrawal. The collision registration can happen before or after the target payout is mined, as long as it happens before `update_finalized_payouts` processes that specific block. This is fully deterministic and repeatable across deposits/operators; the only cost is the bridge deposit amount and Citrea/Bitcoin transaction fees.

### Recommendation
Enforce that `(withdrawal_utxo_txid, withdrawal_utxo_vout)` is unique across `withdrawals` rows (DB constraint plus a check-and-reject in `update_withdrawal_utxo_from_citrea_withdrawal`), and/or change `get_payout_txs_for_withdrawal_utxos` to disambiguate ties (e.g., require the earliest/only registering idx to win, and flag/reject duplicates instead of crediting all of them).

### Proof of Concept
`cargo test` plan (in `core/src/database/verifier.rs` test module, using `create_test_config_with_thread_name`, `insert_block_info`, `insert_txid_to_block`, `insert_spent_utxo`):
1. Create block `block_id`, insert spending tx `txid` spending outpoint `utxo = (txid_u, 0)` via `insert_spent_utxo`.
2. Register idx `i=1`: `upsert_move_to_vault_txid_from_citrea_deposit(1, move_txid_1)`; `update_withdrawal_utxo_from_citrea_withdrawal(1, utxo, block_id)`.
3. Register idx `j=2` with the SAME `utxo`: `upsert_move_to_vault_txid_from_citrea_deposit(2, move_txid_2)`; `update_withdrawal_utxo_from_citrea_withdrawal(2, utxo, block_id)`.
4. Call `get_payout_txs_for_withdrawal_utxos(block_id)` and assert it returns exactly one `(idx, payout_txid)` pair for the single spent UTXO `utxo` — this assertion will FAIL, since it returns two rows `[(1, txid), (2, txid)]` for one actually-spent outpoint.
5. Call `update_payout_txs_and_payer_operator_xonly_pk` with both rows and confirm both idx `1` and `2` now have `payout_txid = txid` set (`get_payout_info_from_move_txid` for both `move_txid_1` and `move_txid_2` returns the same `payout_txid`), demonstrating the duplicate-crediting invariant break.

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

**File:** core/src/verifier.rs (L2248-2262)
```rust
        for (idx, withdrawal_utxo_outpoint) in new_withdrawals {
            tracing::info!(
                "Saving withdrawal utxo {:?} with index {} for Citrea withdrawals",
                withdrawal_utxo_outpoint,
                idx
            );
            self.db
                .update_withdrawal_utxo_from_citrea_withdrawal(
                    Some(dbtx),
                    idx as u32,
                    withdrawal_utxo_outpoint,
                    block_height,
                )
                .await?;
        }
```

**File:** core/src/verifier.rs (L2289-2350)
```rust
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

**File:** core/src/task/payout_checker.rs (L39-79)
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
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-384)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
```
