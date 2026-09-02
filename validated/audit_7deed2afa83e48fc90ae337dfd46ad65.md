### Title
Payout-tx OP_RETURN operator attribution is not covered by the user's signature, letting anyone attribute reimbursement credit to an arbitrary (uninvolved) operator - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` embeds the "operator that fronted the withdrawal" as an unauthenticated OP_RETURN output in the payout transaction [1](#0-0) . The user's authorization signature uses `SinglePlusAnyoneCanPay` sighash, which only commits to the spent input and the correspondingly-indexed output (the user payout output), never to the OP_RETURN output that names the "paying" operator [2](#0-1) . Anyone who funds/finalizes the payout transaction can therefore freely choose which operator's x-only pubkey is embedded, and the bridge later blindly trusts this value to decide who is entitled to claim reimbursement from the vault.

### Finding Description
The payout transaction has: input 0 = user's dust UTXO (spent with the user's `SinglePlusAnyoneCanPay` signature over input 0 + output 0 only), output 0 = user's withdrawal payout, output 1 = anchor, output 2 = OP_RETURN containing `operator_xonly_pk` [3](#0-2) . Because `SinglePlusAnyoneCanPay` explicitly allows anyone to add further inputs/outputs and does not cover output 2, the value written into the OP_RETURN is not bound by the user's signature or by any other cryptographic commitment tying it to the entity that actually supplies the funding inputs (the real "operator" funding is done separately via `fund_raw_transaction` on the caller's own wallet) [4](#0-3) .

On the read side, the chain-sync logic extracts this untrusted value directly from the mined transaction and stores it as ground truth for "who paid": [5](#0-4) 

This stored `payout_payer_operator_xonly_pk` then drives:
- `is_kickoff_malicious`, which trusts it to decide whether a kickoff from a given operator is legitimate [6](#0-5) 
- `validate_payer_is_operator` / `handle_finalized_payout`, used by the operator's own reimbursement flow to determine if *it* is the payer entitled to reimbursement [7](#0-6) 
- `PayoutCheckerTask`, which looks up "first unhandled payout by operator_xonly_pk" and automatically drives the kickoff/reimburse process for whichever operator is named [8](#0-7) 

None of these paths independently verify that the named operator actually supplied the BTC that paid the user; they trust the unauthenticated OP_RETURN field recorded at scan time.

The binding that should hold is:
`payout_payer_operator_xonly_pk == identity of whoever funded the payout output`

But since the OP_RETURN is outside the signature's commitment scope, any funder (an attacker, a competing operator, or a non-operator with sufficient BTC) can complete/fund the payout transaction while writing an arbitrary operator's key into the OP_RETURN. This breaks the equality: an operator who never funded anything can end up recorded — and entitled — as the payer.

### Impact Explanation
Once a party other than the true funder is recorded as `payout_payer_operator_xonly_pk`, the protocol's own reimbursement automation (`PayoutCheckerTask` → `handle_finalized_payout` → kickoff/reimburse chain) will let that (uninvolved) operator claim the `move_to_vault` UTXO funds via `create_reimburse_txhandler`, since the flow only checks that the OP_RETURN pubkey matches the kickoff operator's own key, not that the named operator actually paid [9](#0-8) . This is exactly the Critical bucket "an operator reimbursed for a payout it never funded" — BTC leaves the vault UTXO to reimburse a party with no matching fronted withdrawal.

### Likelihood Explanation
The only requirement is for the attacker (or any third party) to be the one who completes/funds the payout transaction — something the protocol already allows anyone to do because `SinglePlusAnyoneCanPay` was chosen specifically so the withdrawal-funding party can add inputs/outputs freely. No special role, key compromise, or majority collusion is needed; only ordinary transaction construction and broadcast capability, which is available to any Bitcoin network participant that observes the user's off-chain-shared payout signature (routinely delivered to whichever operator services the withdrawal, and visible in the mempool once first broadcast).

### Recommendation
Bind the payer-attribution data cryptographically to the actual funder of the payout transaction, e.g. by covering the OP_RETURN output with a signature from the funding operator's own key (so a `SIGHASH_ALL`/`SIGHASH_SINGLE` commitment from the operator's added input covers it), or by deriving payer identity from which key actually signed the added funding input(s) rather than from an unauthenticated OP_RETURN payload.

### Proof of Concept
1. Operator A prepares to front a withdrawal: it builds `payout_txhandler` with the user's `SinglePlusAnyoneCanPay` signature over input 0/output 0, and its own key in the OP_RETURN, per `create_payout_txhandler` [1](#0-0) , then calls `fund_raw_transaction` to add its own funding inputs [10](#0-9) .
2. Before Operator A's transaction is mined, an attacker observes the same user withdrawal request (or the mempool tx) and constructs a competing transaction reusing the identical committed input 0 and output 0 (unchanged, since the user's `SinglePlusAnyoneCanPay` signature enforces them), but swaps the OP_RETURN payload to embed a different, uninvolved Operator B's x-only pubkey.
3. The attacker funds this alternate transaction with their own BTC inputs (signed by themselves) to cover the withdrawal amount and fees, and broadcasts/gets it mined instead of Operator A's version.
4. During chain sync, `update_finalized_payouts` parses the mined tx's OP_RETURN and records `payout_payer_operator_xonly_pk = Operator B` [5](#0-4) .
5. Operator B's `PayoutCheckerTask` sees an "unhandled payout" attributed to itself [8](#0-7)  and automatically drives `handle_finalized_payout`/kickoff/reimburse, successfully claiming the `move_to_vault` UTXO reimbursement — despite never having funded the withdrawal.

### Citations

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

**File:** core/src/builder/transaction/operator_reimburse.rs (L387-436)
```rust
/// Creates a [`TxHandler`] for the `payout_tx`.
///
/// This transaction is sent by the operator to front a peg-out, after which operator will send a kickoff transaction to get reimbursed.
///
/// # Inputs
/// 1. UTXO: User's withdrawal input (committed in Citrea side, with the signature given to operators off-chain)
///
/// # Outputs
/// 1. User payout output
/// 2. OP_RETURN output (with operators x-only pubkey that fronts the peg-out)
///
/// # Arguments
/// * `input_utxo` - The input UTXO for the payout, committed in Citrea side, with the signature given to operators off-chain.
/// * `output_txout` - The output TxOut for the user payout.
/// * `operator_xonly_pk` - The operator's x-only public key that fronts the peg-out.
/// * `user_sig` - The user's signature for the payout, given to operators off-chain.
/// * `network` - The Bitcoin network.
///
/// # Returns
/// A [`TxHandler`] for the payout transaction, or a [`BridgeError`] if construction fails.
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

**File:** core/src/operator.rs (L614-637)
```rust
        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

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

**File:** core/src/operator.rs (L639-674)
```rust
        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

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

**File:** core/src/operator.rs (L1686-1729)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

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

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2350)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L41-79)
```rust
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
