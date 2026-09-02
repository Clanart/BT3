### Title
Payout tx `OP_RETURN` operator attribution is not covered by the user's payout signature, letting anyone credit (and get an honest operator automatically reimbursed for) a withdrawal that operator never funded - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` embeds the fronting `operator_xonly_pk` in an `OP_RETURN` output of the payout transaction, but the only cryptographic authorization present — the user's `SinglePlusAnyoneCanPay` signature — covers solely input 0 (the user's withdrawal UTXO) and output 0 (the user's payout amount). Neither the extra funding inputs nor the `OP_RETURN` output (index 2) are covered by that signature. Consequently, any party who obtains the user's withdrawal parameters (published as part of Citrea's public `safeWithdraw` call) can construct and broadcast a valid payout transaction that pays the user from its own funds while stamping a completely different (victim) operator's x-only pubkey into the `OP_RETURN`. The verifier's Citrea sync logic and the operator's own automation task then treat that embedded pubkey as ground truth for "who fronted this withdrawal," letting the credited operator automatically claim reimbursement (bridge_amount) from the move-to-vault UTXO for a payout it never made.

### Finding Description
`create_payout_txhandler` builds the payout transaction with:
- input 0: the withdrawal UTXO, spent via `SpendPath::KeySpend` using the user's pre-given signature,
- output 0: the user's payout,
- output 2: an `OP_RETURN` containing `operator_xonly_pk`, the pubkey of whoever claims to have fronted the peg-out. [1](#0-0) 

`Operator::withdraw` verifies the user's signature against the sighash of input 0 only, and explicitly documents that the signature must use the `SinglePlusAnyoneCanPay` sighash type: [2](#0-1) 

Under `SIGHASH_SINGLE | ANYONECANPAY`, the user's signature commits only to input 0's prevout and to the output at the same index (output 0). It does not commit to any other input (so anyone can add funding inputs via `fund_raw_transaction`, exactly as `Operator::withdraw` itself does) and does not commit to output 1 (anchor) or output 2 (the `OP_RETURN` carrying `operator_xonly_pk`). This means the identity written into the `OP_RETURN` is entirely at the discretion of whoever assembles and funds the final transaction — it is not bound to whoever actually supplies the BTC that pays the user. [3](#0-2) 

Downstream, when this payout tx confirms, the verifier's Citrea-sync path parses the `OP_RETURN` and records its contents as the authoritative "payer" for the withdrawal, with no check that the named operator supplied the funding inputs: [4](#0-3) 

That attribution is persisted via `update_payout_txs_and_payer_operator_xonly_pk` and later looked up per-operator by `get_first_unhandled_payout_by_operator_xonly_pk`: [5](#0-4) [6](#0-5) 

The operator's own background automation (`PayoutCheckerTask`) blindly trusts this DB record: once *any* payout is attributed to the operator's `xonly_pk`, it automatically calls `handle_finalized_payout` and proceeds toward reimbursement (kickoff, then eventually `reimburse_tx`, which pays the operator from the `MoveToVaultTx` output) — with no verification step confirming the operator itself broadcast/funded that specific payout tx: [7](#0-6) 

The only sanity check performed later, `is_kickoff_malicious`, merely checks that `operator_xonly_pk` in the `OP_RETURN` matches the `kickoff_data.operator_xonly_pk` used for the kickoff — which trivially passes, since the attacker chose to embed exactly the victim operator's pubkey: [8](#0-7) 

The withdrawal parameters needed to construct this payout tx (the input outpoint, the user's signature, the output script/amount) are transmitted through Citrea's public `safeWithdraw` contract call, which is visible to any observer of the Citrea chain, not just to a designated operator: [9](#0-8) 

This is the same root-cause class as the `FeeAuction.buy` report: a value-moving action (`buy` paying without validating the return leg; here, a payout tx crediting reimbursement without validating who actually funded it) lacks a binding between "the party that pays" and "the party that is credited/paid."

### Impact Explanation
This breaks the custody binding "the operator credited for fronting a withdrawal" == "the party that actually paid the withdrawal." An unprivileged attacker can pay a pending Citrea withdrawal from their own BTC while attributing the fronting credit to an arbitrary registered operator. That operator's own automation will then autonomously pursue kickoff/reimbursement and, absent a challenge that specifically detects this discrepancy, will be reimbursed bridge_amount BTC from the move-to-vault UTXO for a payout it never funded — matching the Critical impact "an operator reimbursed for a payout it never funded" / BTC leaving a move-to-vault UTXO without a matching fronted withdrawal by the credited party.

### Likelihood Explanation
The withdrawal parameters (outpoint, user signature, destination, amount) are exposed through a public Citrea contract call, so no privileged access or off-chain leak is required to obtain them. Constructing the competing payout transaction only requires standard Bitcoin wallet capabilities (funding inputs and broadcasting), which any unprivileged actor has. The operator-side automation (`PayoutCheckerTask`) acts on the `OP_RETURN` attribution without further validation, so the exploit does not require compromising any verifier, operator, or aggregator role — it only requires being faster to broadcast a self-funded payout tx with someone else's pubkey in the `OP_RETURN`.

### Recommendation
Bind the `operator_xonly_pk` (and the funding inputs) to the same authorization the user provides for the payout, e.g. by having the user's signature (or a separate protocol-level commitment recorded during withdrawal registration on Citrea) also cover/commit to the operator identity that is allowed to claim credit, or by requiring the operator to co-sign/commit its identity as part of the same signed message the user provides off-chain, so a third party cannot substitute an arbitrary `OP_RETURN` payload while reusing the pre-signed withdrawal input.

### Proof of Concept
1. Attacker observes a pending Citrea `safeWithdraw` transaction and extracts `input_outpoint`, `in_signature` (`SIGHASH_SINGLE|ANYONECANPAY`), `out_script_pubkey`, `out_amount` — all public on Citrea. (`core/src/test/common/citrea/mod.rs:415-443`)
2. Attacker builds a payout transaction identical in structure to `create_payout_txhandler`, but sets `operator_xonly_pk` to a victim operator's registered pubkey instead of their own. (`core/src/builder/transaction/operator_reimburse.rs:407-436`)
3. Attacker funds the transaction with their own BTC UTXOs (equivalent to the `fund_raw_transaction`/`add_inputs` step that `Operator::withdraw` itself performs) and applies the user's pre-existing `in_signature` on input 0. (`core/src/operator.rs:620-674`)
4. Attacker broadcasts the transaction; it is valid because the user's signature only constrains input 0/output 0 under `SIGHASH_SINGLE|ANYONECANPAY`.
5. On confirmation, `update_finalized_payouts` reads the victim operator's pubkey from the `OP_RETURN` and records them as the payer. (`core/src/verifier.rs:2312-2342`, `core/src/database/verifier.rs:198-251`)
6. The victim operator's `PayoutCheckerTask` picks up this "unhandled payout" for its own `xonly_pk` and automatically proceeds through kickoff/reimbursement, eventually receiving `bridge_amount` from the `MoveToVaultTx` output despite never having funded the payout. (`core/src/task/payout_checker.rs:39-106`)

### Citations

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

**File:** core/src/operator.rs (L620-637)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

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

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
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
```

**File:** core/src/verifier.rs (L2312-2342)
```rust
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

**File:** core/src/test/common/citrea/mod.rs (L432-443)
```rust
        let citrea_withdrawal_tx = e2e
            .citrea_client
            .contract
            .safeWithdraw(params.0, params.1, params.2, params.3, params.4)
            .nonce(current_nonce)
            .value(U256::from(
                e2e.config.protocol_paramset().bridge_amount.to_sat() * SATS_TO_WEI_MULTIPLIER,
            ))
            .send()
            .await
            .unwrap();
        tracing::info!("Withdrawal TX sent in Citrea");
```
