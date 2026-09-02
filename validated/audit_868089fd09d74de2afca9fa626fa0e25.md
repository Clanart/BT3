### Title
Aliased `withdrawal_utxo` across two withdrawal indices lets a single payout transaction be credited as fronting two separate deposits, causing double reimbursement - ([File: core/src/database/verifier.rs])

### Summary
`get_payout_txs_for_withdrawal_utxos` and `update_finalized_payouts` associate a confirmed payout transaction to a withdrawal purely by the raw `(withdrawal_utxo_txid, withdrawal_utxo_vout)` pair, with no DB constraint or check that this pair is unique to a single withdrawal `idx`/deposit. If two different withdrawal indices (backing two different deposits/vaults) are registered with the identical Bitcoin UTXO as their payout input, operator X's single payout transaction spending that UTXO gets matched to *both* withdrawal rows, both get `payout_payer_operator_xonly_pk = X`, and `validate_payer_is_operator`/`get_reimbursement_txs` will happily authorize a Reimburse transaction against each of the two deposits' move-to-vault UTXOs for what was in reality only one funded payout.

### Finding Description
The equality that must hold is: **(operator credited/reimbursed for withdrawal idx) == (party whose BTC funded exactly that withdrawal's payout, counted once)**.

The binding is enforced (or rather, not enforced) as follows:

- `withdrawals` table has no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` [1](#0-0) , only `idx` is the primary key.
- `Verifier::update_finalized_payouts` finds all withdrawal rows whose `withdrawal_utxo` was spent in a synced block via `get_payout_txs_for_withdrawal_utxos`, which joins `withdrawals` to `bitcoin_syncer_spent_utxos` strictly `ON bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout` [2](#0-1) . If two withdrawal `idx` rows (idx_a for deposit/vault A, idx_b for deposit/vault B) share the same `withdrawal_utxo_txid:vout`, this query returns **both** rows mapped to the same spending `payout_txid` once that UTXO is spent by any transaction (regardless of which withdrawal "intended" it).
- `update_payout_txs_and_payer_operator_xonly_pk` then writes the operator's xonly pubkey (parsed from the payout tx's OP_RETURN) into `payout_payer_operator_xonly_pk` for **both** rows [3](#0-2) [4](#0-3) .
- `PayoutCheckerTask` then picks up each unhandled payout by `payout_payer_operator_xonly_pk = X` independently and calls `handle_finalized_payout` for each deposit, marking each handled with its own `kickoff_txid` [5](#0-4) .
- `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` is queried per `deposit_id`, joining `deposits.move_to_vault_txid = withdrawals.move_to_vault_txid` [6](#0-5) , so it independently returns `payer_xonly_pk == X` for deposit A and deposit B, since each was written independently by the aliasing above.
- `Operator::validate_payer_is_operator` only checks `payer_xonly_pk == self.signer.xonly_public_key` [7](#0-6) ; it never checks that the underlying payout transaction's spent input is unique to this specific deposit's withdrawal_utxo record, or that the payout hasn't already been consumed for another deposit's reimbursement.
- `Operator::get_reimbursement_txs` then walks the presigned tx graph and eventually returns/enables the Reimburse tx per deposit outpoint independently [8](#0-7) , spending each deposit's own move-to-vault UTXO via `create_reimburse_txhandler` [9](#0-8) .

No existing guard closes this gap: `Verifier::is_kickoff_malicious` only checks that the payout OP_RETURN operator matches the kickoff operator and that the committed payout blockhash matches [10](#0-9)  — it never checks that the payout transaction's spent input UTXO is uniquely tied to *this* deposit's withdrawal_utxo record versus another deposit's. The bridge-circuit-level binding of payout input to a specific deposit's storage-proof index (`bridge_circuit`) only runs during a challenge/disprove, not during ordinary reimbursement, so an unchallenged operator is reimbursed twice before any circuit-level check occurs.

### Impact Explanation
Critical: BTC leaves a move-to-vault UTXO without a matching fronted withdrawal. Operator X constructs and broadcasts exactly one payout transaction (fronting one user's peg-out with the shared/aliased UTXO), yet is credited as the payer for two separate deposits' withdrawal rows, and can drive both deposits through kickoff/reimbursement to collect two Reimburse payouts — one legitimately earned, one obtained without ever fronting BTC for that specific vault. This directly matches "an operator reimbursed for a payout it never funded" and is repeatable across any number of deposits that end up sharing an aliased `withdrawal_utxo`, and across any operator, since the flaw is in the shared verifier/operator database logic, not operator-specific code.

### Likelihood Explanation
This requires that two different Citrea withdrawal indices end up registered against the identical Bitcoin `withdrawal_utxo_txid:vout` — the attacker capability list explicitly allows "choose the bytes of a withdrawal UTXO" when calling `withdraw` on the Citrea Bridge contract, and neither `collect_withdrawal_utxos`/`update_citrea_deposit_and_withdrawals` nor the `withdrawals` schema in this repo enforce uniqueness of that pair across indices [11](#0-10) [12](#0-11) . Cost is limited to two deposits' bridge amounts plus normal Bitcoin fees for one payout transaction; the payoff is a full extra reimbursement per aliased pair, making it economically attractive and repeatable.

### Recommendation
Scope the payout-to-withdrawal matching by withdrawal `idx`/`deposit_id`, not solely by `(withdrawal_utxo_txid, withdrawal_utxo_vout)`: add a DB uniqueness constraint on that pair in `withdrawals`, and/or have `validate_payer_is_operator`/`handle_finalized_payout` verify that the payout transaction being credited has not already been consumed to satisfy a different deposit's reimbursement before marking it handled or authorizing a Reimburse tx.

### Proof of Concept
`cargo test` plan (regtest, `MockCitreaClient`, no mainnet/live Citrea), styled after `core/src/test/deposit_and_withdraw_e2e.rs`:
1. Create two deposits A and B (`make_concurrent_deposits` style), each with its own `move_to_vault_txid`.
2. Generate a single dust UTXO `U` and register it via `MockCitreaClient::insert_withdrawal_utxo` for **both** withdrawal index `idx_a` and `idx_b` (asserting DB has two `withdrawals` rows with identical `withdrawal_utxo_txid`/`vout` but different `move_to_vault_txid`).
3. Have operator X call `withdraw` once for `idx_a`, broadcast and confirm the resulting payout tx (`DEFAULT_FINALITY_DEPTH` blocks).
4. Run btc-syncer/`update_finalized_payouts` and assert `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` returns `payer_xonly_pk == X` for **both** deposit A and deposit B.
5. Drive `PayoutCheckerTask` to completion and call `operator.get_reimbursement_txs(deposit_outpoint_a)` and `operator.get_reimbursement_txs(deposit_outpoint_b)` through to completion, broadcasting all returned txs.
6. Assert two distinct `Reimburse` transactions confirm on regtest (one per deposit's move-to-vault UTXO) while `rpc.get_txid_where_utxo_is_spent(U)` shows only one payout transaction ever existed for `U`.

### Citations

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

**File:** core/src/database/verifier.rs (L315-346)
```rust
    pub async fn get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(Option<XOnlyPublicKey>, Option<BlockHash>, Option<Txid>), BridgeError> {
        let query = sqlx::query_as::<
            _,
            (
                Option<XOnlyPublicKeyDB>,
                Option<BlockHashDB>,
                Option<TxidDB>,
            ),
        >(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.kickoff_txid
             FROM withdrawals w
             INNER JOIN deposits d ON d.move_to_vault_txid = w.move_to_vault_txid
             WHERE d.deposit_id = $1",
        )
        .bind(i32::try_from(deposit_id).wrap_err("Failed to convert deposit id to i32")?);

        let result: (
            Option<XOnlyPublicKeyDB>,
            Option<BlockHashDB>,
            Option<TxidDB>,
        ) = execute_query_with_tx!(self.connection, tx, query, fetch_one)?;

        Ok((
            result.0.map(|pk| pk.0),
            result.1.map(|block_hash| block_hash.0),
            result.2.map(|txid| txid.0),
        ))
    }
```

**File:** core/src/verifier.rs (L1859-1914)
```rust
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
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

**File:** core/src/verifier.rs (L2296-2350)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L39-111)
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

        dbtx.commit().await?;

        Ok(true)
    }
```

**File:** core/src/operator.rs (L1703-1729)
```rust
        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
```

**File:** core/src/operator.rs (L2098-2150)
```rust
    pub async fn get_reimbursement_txs(
        &self,
        deposit_outpoint: OutPoint,
    ) -> Result<Vec<(TransactionType, Transaction)>, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        // first check if the deposit is in the database
        let (deposit_id, mut deposit_data) = self
            .db
            .get_deposit_data(Some(&mut dbtx), deposit_outpoint)
            .await?
            .ok_or_eyre(format!(
                "Deposit data not found for the requested deposit outpoint: {deposit_outpoint:?}, make sure you send the deposit outpoint, not the move txid."
            ))?;

        tracing::info!(
            "Deposit data found for the requested deposit outpoint: {deposit_outpoint:?}, deposit id: {deposit_id:?}",
        );

        // validate payer is operator and get payer xonly pk, payout blockhash and kickoff txid
        let (payout_blockhash, kickoff_txid) = self
            .validate_payer_is_operator(Some(&mut dbtx), deposit_id)
            .await?;

        let mut current_round_idx = self.db.get_current_round_index(Some(&mut dbtx)).await?;

        let mut txs_to_send: Vec<(TransactionType, Transaction)>;

        loop {
            txs_to_send = self
                .get_next_txs_to_send(
                    Some(&mut dbtx),
                    &mut deposit_data,
                    payout_blockhash,
                    kickoff_txid,
                    current_round_idx,
                )
                .await?;
            if txs_to_send.is_empty() {
                // if no txs were returned, and we advanced the round in the db, ask for the next txs again
                // with the new round index
                let round_idx_after_operations =
                    self.db.get_current_round_index(Some(&mut dbtx)).await?;
                if round_idx_after_operations != current_round_idx {
                    current_round_idx = round_idx_after_operations;
                    continue;
                }
            }
            break;
        }

        dbtx.commit().await?;
        Ok(txs_to_send)
    }
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-385)
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
}
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
