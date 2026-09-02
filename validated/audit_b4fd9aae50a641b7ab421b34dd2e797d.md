### Title
Payout tx's operator-attribution `OP_RETURN` is unsigned, letting the real BTC payer misattribute reimbursement credit to any operator - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The `payout_tx`'s `OP_RETURN` output, which the protocol uses as the sole on-chain record of which operator fronted a Citrea withdrawal, is never covered by the user's withdrawal signature. Because the user signs with `SIGHASH_SinglePlusAnyoneCanPay`, whoever actually supplies the funding inputs for the payout (the real "payer") can freely choose an arbitrary `xonly_pk` to place in the `OP_RETURN`, decoupling "who the bridge credits as payer" from "who actually paid." The verifier and `PayoutCheckerTask`/`is_kickoff_malicious` logic trust this `OP_RETURN` value as ground truth, so an operator can be credited (and subsequently reimbursed through the kickoff/reimburse flow) for a payout they never funded.

### Finding Description
`create_payout_txhandler` builds the `payout_tx` with three outputs: the user's payout (index 0), an anchor (index 1), and an `OP_RETURN` containing the fronting operator's `xonly_pk` (index 2): [1](#0-0) 

The only signature present on this transaction is the user's withdrawal signature over the dust input, which is enforced to use `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1) 

`SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0, the user's payout), and `ANYONECANPAY` excludes all other inputs from commitment. This is confirmed by the verification logic: [3](#0-2) 

As a result, outputs 1 (anchor) and 2 (`OP_RETURN`, the operator attribution) are **not** covered by any signature. Whoever actually supplies the extra funding inputs to cover the withdrawal amount (via `fund_raw_transaction`) — the real "payer" — is free to write *any* `xonly_pk` into the `OP_RETURN`, including one belonging to a different registered operator who never funded anything.

This attribution is later trusted as ground truth by the verifier when the payout confirms: [4](#0-3) 

which is stored as `payout_payer_operator_xonly_pk` in the `withdrawals` table: [5](#0-4) 

The `PayoutCheckerTask` then automatically drives that named operator through `handle_finalized_payout` and the reimbursement pipeline based solely on this unauthenticated value: [6](#0-5) 

Crucially, `is_kickoff_malicious` — the check meant to ensure only the true payer can extract reimbursement — only verifies that the kickoff's `operator_xonly_pk` matches the `OP_RETURN` value stored in the DB; it has no way to verify that the named operator actually supplied the BTC: [7](#0-6) 

Since the named operator's own kickoff will trivially match this stored, attacker-chosen value (`operator_xonly_pk == kickoff_data.operator_xonly_pk`), the reimbursement flow proceeds as if that operator had fronted the withdrawal, even though the actual BTC came from someone else.

This breaks the equality that should hold: `payout_payer_operator_xonly_pk (bookkeeping) == entity that funded the payout_tx inputs (on-chain reality)`. The report's LienToken analog is the same class of bug — an actor-controlled, unauthenticated field (`payee`/`OP_RETURN` operator pubkey) is used as the sole source of truth for a custody/attribution decision (PublicVault bookkeeping / bridge reimbursement bookkeeping) without any binding that ties it to the party who actually transferred value.

### Impact Explanation
This is Critical: it lets the value paid to fund a withdrawal become decoupled from the bookkeeping used to authorize reimbursement, i.e., an operator can be reimbursed (via the N-of-N kickoff/challenge/reimburse chain) for a payout it never funded, while whoever actually fronted the withdrawal receives no bookkeeping credit for it. This directly matches the listed critical impact "an operator reimbursed for a payout it never funded."

### Likelihood Explanation
Exploitation requires the attacker to be the party constructing/funding the `payout_tx` (i.e., possess and spend the required extra BTC inputs to cover the withdrawal amount) and to simply write a different `xonly_pk` into the unsigned `OP_RETURN` output before broadcasting — no special role, key compromise, or verifier/operator privilege is required; anyone able to observe a pending withdrawal (via Citrea/aggregator) and willing to front the BTC can pick which operator gets credited. This is a pure code-path issue rooted in the choice of `SinglePlusAnyoneCanPay` and the absence of any commitment on the `OP_RETURN` output.

### Recommendation
Bind the operator-attribution output to the same signature that authorizes the withdrawal, e.g. by having the user sign with a sighash type that also commits to the `OP_RETURN` output (or by using a separate covenant/commitment mechanism verified on-chain), so that whoever supplies the funding inputs cannot arbitrarily choose which operator's pubkey is recorded as the payer. Alternatively, require the verifier's `is_kickoff_malicious` check to additionally verify that the funding inputs of the confirmed `payout_tx` are attributable to the operator named in the `OP_RETURN` (e.g., via a pre-registered funding key/address per operator).

### Proof of Concept
1. A withdrawal is registered on Citrea; the user's `SinglePlusAnyoneCanPay` signature over the dust input + payout output is obtained (per `parse_withdrawal_sig_params`).
2. Party X (not the intended/registered payer for this withdrawal) constructs a `payout_tx` using `create_payout_txhandler`, reusing the valid user signature for input 0, funding the required extra inputs from their own wallet to cover `output_txout`, but sets the `OP_RETURN` (`op_return_txout`) to embed operator `B`'s `xonly_pk` instead of their own.
3. Since the signature is `SinglePlusAnyoneCanPay`, it remains valid regardless of the `OP_RETURN` content or which extra inputs are added; X broadcasts this transaction and it confirms.
4. `update_finalized_payouts` (`core/src/verifier.rs:2283`) parses the `OP_RETURN`, records `payout_payer_operator_xonly_pk = B`.
5. `PayoutCheckerTask` (`core/src/task/payout_checker.rs`) triggers `operator B`'s `handle_finalized_payout`; `is_kickoff_malicious` (`core/src/verifier.rs:1857`) sees `operator_xonly_pk (B) == kickoff_data.operator_xonly_pk (B)` and accepts the kickoff as legitimate.
6. Operator B proceeds through the round/kickoff/reimburse flow and is reimbursed the bridge amount for a payout that X, not B, actually funded.

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

**File:** core/src/rpc/parser/operator.rs (L161-188)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

    // If the Taproot sighash type is Default (no explicit type attached; i.e. a 64-byte
    // signature without a sighash flag), normalize it to SinglePlusAnyoneCanPay.
    // Prior to v0.5 this was Clementine's implicit behavior; we retain it here for
    // backwards compatibility when a 64-byte signature is provided.
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }

```

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/verifier.rs (L1857-1915)
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
