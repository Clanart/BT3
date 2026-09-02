### Title
Unauthenticated OP_RETURN operator attribution in `payout_tx` lets anyone reassign reimbursement credit away from the actual payer - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `payout_tx` that fronts a Citrea withdrawal only signs the user's withdrawal UTXO under a `SIGHASH_SINGLE | ANYONECANPAY` signature. The OP_RETURN output that records "which operator fronted this payout" is unsigned data supplied by whoever assembles the final transaction, not the party who actually supplies the (near full bridge-amount) funding inputs. Any unprivileged party who observes a broadcast/mempool payout transaction can rebuild it with their own funding inputs and an arbitrary operator's x-only pubkey in the OP_RETURN, and get it mined instead. The chain-scanning code then blindly trusts that OP_RETURN value as the "payer," letting the credited operator's own automation claim reimbursement for a payout it never fronted.

### Finding Description
`create_payout_txhandler` builds the payout transaction with a single signed input (the user's withdrawal UTXO, key-spent with `user_sig`), and separately appends an OP_RETURN output containing whatever `operator_xonly_pk` the caller passes in: [1](#0-0) 

This `user_sig` is required to use the `SinglePlusAnyoneCanPay` sighash type, as enforced in multiple places: [2](#0-1) 

Under `SIGHASH_SINGLE|ANYONECANPAY`, the signature only commits input 0 to its paired output (the user payout output); it does not constrain any additional funding inputs added later via RBF/`fund_raw_transaction`, nor the anchor/OP_RETURN outputs: [3](#0-2) 

Consequently, any party — operator or not — who can observe a payout transaction containing this signature (e.g. once it hits the mempool, or via the aggregator's broadcast of the same withdrawal params to multiple operators) can rebuild an equivalent transaction that: (a) keeps input 0/output 0 identical to satisfy the signature, (b) supplies its own funding inputs to cover the payout amount, and (c) writes a completely different operator's x-only pubkey into the OP_RETURN.

The verifier's block-sync code then trusts this OP_RETURN value unconditionally as the payer's identity, with no check that the additional funding inputs are controlled by, or attributable to, that operator: [4](#0-3) [5](#0-4) 

This value is later used verbatim by the framed operator's own automation to decide it is the legitimate payer: [6](#0-5) [7](#0-6) 

The only cross-check that exists, `is_kickoff_malicious`, merely verifies that the operator who *later* sends a Kickoff matches the `payout_payer_operator_xonly_pk` already recorded in the DB — it does not verify that the recorded payer actually supplied the payout funds: [8](#0-7) 

So the binding "operator credited with a payout" == "operator who actually fronted the BTC" is never enforced; it is entirely inferred from unauthenticated OP_RETURN bytes that anyone funding the transaction can set arbitrarily.

### Impact Explanation
If an attacker (no special role required — just Bitcoin capital and the ability to observe a broadcast payout tx) rebuilds the payout transaction with a different operator's pubkey in the OP_RETURN and gets it confirmed, the chain-sync logic records that *other*, honest operator as the payer. That honest operator's own `PayoutCheckerTask`/`#[cfg(feature = "automation")]` flow will then automatically detect this "unhandled payout" under its own key and proceed through kickoff/reimburse, ultimately paying itself the deposit's bridge amount from the move-to-vault UTXO — despite never funding the withdrawal. This is exactly the "operator reimbursed for a payout it never funded" scenario, which the custody model must prevent (Critical impact class), and it stems from a broken equality: `payout_payer_operator_xonly_pk (recorded) == funder_of_additional_inputs (actual)` is assumed but never checked or signed.

### Likelihood Explanation
Exploitation requires the attacker to supply funding equal to the withdrawal payout amount (near the full bridge amount) themselves, and to win a fee/mempool race against the legitimate payer's transaction (or act before any legitimate payer broadcasts). This capital requirement lowers the likelihood of a purely profit-motivated attack, but it does not require any protocol role (operator/verifier/etc.) — only the ability to observe the signed withdrawal request (broadcast identically to potentially many operators by the aggregator's `Withdraw` RPC) or a broadcasted mempool transaction, and to fund/rebroadcast a competing transaction. It can be used to grief specific operators (forcing them into kickoff/challenge/collateral flows for withdrawals they never intended to pay) or to arbitrarily redirect the reimbursement credit for a withdrawal to any registered operator of the attacker's choosing.

### Recommendation
Bind the OP_RETURN operator attribution cryptographically to the entity that actually funds the payout, e.g. by having the operator sign the whole transaction (including the OP_RETURN output and its own funding inputs) with its own key, and have the chain-sync/`is_kickoff_malicious` logic verify that signature rather than trusting bare OP_RETURN bytes. Alternatively, require the additional funding inputs in the payout transaction to be provably spent from a UTXO set pre-registered/committed to a specific operator (e.g. the operator's collateral wallet), and reject attribution to any operator whose known funding inputs are absent from the confirmed transaction.

### Proof of Concept
1. Operator/aggregator broadcasts (or an attacker observes in mempool) a withdrawal request with the user's `SIGHASH_SINGLE|ANYONECANPAY` signature over the withdrawal UTXO and the destination output, per `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`).
2. Attacker copies input 0 and output 0 (both covered by the signature), adds its own funding UTXO(s) to cover `out_amount`, adds the anchor output, and appends an OP_RETURN with an arbitrary registered operator B's x-only pubkey (instead of the real payer's).
3. Attacker broadcasts this transaction with a higher fee so it confirms instead of/before the legitimate one.
4. `update_finalized_payouts` in `core/src/verifier.rs:2283-2353` parses the OP_RETURN and records `payout_payer_operator_xonly_pk = B`.
5. Operator B's own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-79`) picks up this "unhandled payout" under its own key, and `validate_payer_is_operator` (`core/src/operator.rs:1703-1719`) succeeds because `payer_xonly_pk == B.signer.xonly_public_key`.
6. B's automation proceeds through Kickoff/Reimburse and receives the move-to-vault bridge amount, despite never having funded the withdrawal.

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

**File:** core/src/verifier.rs (L1632-1637)
```rust
            })?;

        // amount in move_tx is exactly the bridge amount
        if output_amount
            > self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
        {
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

**File:** core/src/verifier.rs (L2312-2343)
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
        }
```

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```

**File:** core/src/operator.rs (L1703-1719)
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
