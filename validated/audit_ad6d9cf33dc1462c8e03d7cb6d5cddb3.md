### Title
Payout confirmation credits any real operator's xonly_pk from OP_RETURN with no proof that operator funded the payout, letting an attacker self-fund a withdrawal and frame an uninvolved operator for reimbursement - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`create_payout_txhandler` binds only the withdrawal signature (input 0, output 0) via `SinglePlusAnyoneCanPay`; the OP_RETURN `operator_xonly_pk` and any additional fee/change inputs are completely unauthenticated and unbound to each other. Because the withdrawal signature uses `ANYONECANPAY`, any party (including the withdrawer/attacker) can independently add funding inputs and broadcast the payout tx, writing an arbitrary real operator's xonly_pk into the OP_RETURN. `Verifier::update_finalized_payouts` records this OP_RETURN pk as `payout_payer_operator_xonly_pk` purely by chain-parsing, with no check that the named operator supplied the extra inputs, and `PayoutCheckerTask`/`Operator::handle_finalized_payout` then automatically drive that operator into kickoff + `create_reimburse_txhandler`, crediting bridge_amount to an operator that never fronted anything.

### Finding Description
The broken binding, stated explicitly: the equality the protocol needs is

`operator_xonly_pk in OP_RETURN of payout_tx == entity whose BTC funded output 0 (the payout)`.

Tracing shows this equality is never enforced anywhere:

1. `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with input 0 = `input_utxo` (`SpendPath::KeySpend`, signed by `user_sig`), output 0 = `output_txout` (the withdrawer's payment), output 1 = anchor, output 2 = `op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()))`. The `operator_xonly_pk` bytes are not part of the message signed by `user_sig`, and are pushed unconstrained into OP_RETURN. [1](#0-0) 
2. The user signature `in_signature` uses `SinglePlusAnyoneCanPay` per docstring and is verified only against `sighash` covering input/output index 0 (`SECP.verify_schnorr(... sighash ... )` in `Operator::withdraw`) — `ANYONECANPAY` explicitly permits any third party to append additional funding inputs without invalidating the signature. [2](#0-1) 
3. In the legitimate flow, `Operator::withdraw` funds the transaction via its own bitcoind wallet (`self.rpc.fund_raw_transaction(...)`), so the extra inputs come from that operator's own funds — but this is only a convention of that one code path, not a cryptographic or database-enforced binding. [3](#0-2) 
4. Because the attacker (the withdrawer, who controls the private key of `input_utxo`'s script and picks the sighash flag per the rules) can sign input 0 themselves and then add their own separate BTC as extra inputs, they can build and broadcast this exact transaction directly to Bitcoin without ever calling any operator's or the aggregator's gRPC, naming any real operator X's pk in the OP_RETURN.
5. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) discovers the payout tx purely by matching the registered `withdrawal_utxo` outpoint (via `get_payout_txs_for_withdrawal_utxos`, a plain SQL join on `bitcoin_syncer_spent_utxos`), then parses the OP_RETURN for `operator_xonly_pk` and writes it to `withdrawals.payout_payer_operator_xonly_pk` with no verification that X supplied any of the transaction's inputs. [4](#0-3) [5](#0-4) 
6. `get_first_unhandled_payout_by_operator_xonly_pk` selects unhandled rows purely by that DB column, and `PayoutCheckerTask::run_once` uses it to automatically call `Operator::handle_finalized_payout` for operator X — this call unconditionally fetches X's own unused/signed kickoff connector and creates/sends a kickoff, with no check that X ever broadcast or funded the discovered payout tx. [6](#0-5) [7](#0-6) [8](#0-7) 
7. `Verifier::is_kickoff_malicious` only re-checks that `operator_xonly_pk` in the payout OP_RETURN equals `kickoff_data.operator_xonly_pk` and that the committed blockhash matches — it never checks that X's own wallet/collateral funded output 0's extra inputs. [9](#0-8) 
8. `create_reimburse_txhandler` then spends the move-to-vault UTXO's `bridge_amount` to `operator_reimbursement_address` once X completes the kickoff/round flow uncontested. [10](#0-9) 

Existing guards fail to close this gap: `SECP.verify_schnorr` only proves the withdrawer authorized paying exactly output 0's value/script from input 0 — it says nothing about who paid the rest of the inputs or whose pubkey appears in OP_RETURN. `is_kickoff_malicious` only cross-checks two on-chain-derived values against each other (both attacker-controlled), not against any operator-side funding record.

### Impact Explanation
An uninvolved, named operator X ends up eligible to run `create_reimburse_txhandler` and drain `bridge_amount` from the deposit's move-to-vault UTXO for a withdrawal it never funded — directly matching the listed Critical category "an operator reimbursed for a payout it never funded." X's automated `PayoutCheckerTask` will unconditionally attempt this (consuming one of X's limited per-round kickoff connectors and exposing X to the assert/challenge machinery) purely because an attacker chose to write X's public key into an OP_RETURN byte string. This is repeatable across any withdrawal and any operator whose xonly_pk is publicly known (all operator keys are public), since nothing in the discovery or crediting path is operator-specific beyond the OP_RETURN bytes.

### Likelihood Explanation
The attack requires only: (1) the attacker be the withdrawer (or otherwise control the private key behind `input_utxo`'s script_pubkey and choose `SinglePlusAnyoneCanPay`), which the threat model explicitly grants ("choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag"), and (2) enough of the attacker's own BTC to cover output 0's value and transaction fees. No operator, verifier, aggregator, or Citrea privilege is needed, and no gRPC call to any Clementine service is required at all — the attacker can build and broadcast the transaction with any standard Bitcoin tooling. This is fully feasible and repeatable at the cost of the attacker's own withdrawal-value BTC.

### Recommendation
Bind the OP_RETURN-named operator to the actual funding of the payout. Concrete options: (a) require the operator's reimbursement eligibility to be established by the operator's own signed record of having submitted this specific payout txid (cross-check `payout_txid` against the operator's own `tx_sender`/broadcast history before crediting `payout_payer_operator_xonly_pk`, rather than trusting on-chain OP_RETURN parsing alone), and/or (b) redesign the payout transaction so the reimbursement-eligible operator's own UTXO/collateral must be a mandatory, script-enforced input (not just OP_RETURN bytes), so that reimbursement eligibility is cryptographically tied to actual fund outflow from that operator, not to arbitrary unauthenticated metadata.

### Proof of Concept
```
cargo test -p core payout_attribution_without_funding -- --nocapture
```
Plan for the test (regtest, no mainnet/live Citrea, using `MockCitreaClient`/test harness already present in `core/src/test/`):
1. Run a normal deposit for some `deposit_outpoint` via `run_single_deposit`, and register a withdrawal (`insert_withdrawal_utxo`) using a dust UTXO fully controlled by the "attacker" test identity (not any operator).
2. As the attacker, sign the withdrawal UTXO with `SinglePlusAnyoneCanPay` for a payout `output_txout` paying the withdrawer address.
3. Build the payout transaction directly with `create_payout_txhandler`, passing `operator_xonly_pk = actors.get_operator_by_index(0)`'s real xonly_pk (an operator who did nothing), and fund/complete the extra inputs from a *separate* attacker-owned wallet UTXO (not from operator 0's wallet) using `fund_raw_transaction`-equivalent test helpers, then broadcast directly via `rpc` (bypassing `operator.withdraw`/`operator.internal_withdraw` entirely).
4. Mine to finality; wait for `Verifier`'s bitcoin syncer to call `update_finalized_payouts` and assert `db.get_payout_info_from_move_txid(...)` returns `Some(operator_0_xonly_pk, ...)`.
5. Assert operator 0's `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk` picks up this withdrawal and that `handle_finalized_payout` succeeds, producing a kickoff txid, and that operator 0's DB row is eventually marked `is_payout_handled = true` with a `kickoff_txid`, all while asserting operator 0's wallet balance/UTXO set was untouched by the payout tx (proving operator 0 fronted zero funds) — asserting the binding `payout_payer_operator_xonly_pk == entity that funded output 0` is violated: the recorded value is operator 0's pk, but the actual funder was the attacker's separate wallet.
6. Optionally continue the flow to `create_reimburse_txhandler` broadcast and assert the move-to-vault UTXO's `bridge_amount` is paid to operator 0's `operator_reimbursement_address`, completing the "reimbursed for a payout never funded" demonstration.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L373-384)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L651-674)
```rust
        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
```

**File:** core/src/operator.rs (L839-884)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }
```

**File:** core/src/verifier.rs (L1882-1914)
```rust
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

**File:** core/src/verifier.rs (L2311-2342)
```rust
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
