## Title
Payout attribution can be forged because the operator identity is carried in an unsigned OP_RETURN output while the user's authorization uses `SIGHASH_SINGLE|ANYONECANPAY` - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` embeds the operator's x-only public key in a plaintext OP_RETURN output that is used later by verifiers as the authoritative record of "which operator fronted this withdrawal." The only cryptographic authorization present on the transaction is the user's `SinglePlusAnyoneCanPay` signature over input 0 / output 0. That sighash flag combination does **not** cover any other input or output of the transaction, including the OP_RETURN output that carries the operator attribution, so the equality "OP_RETURN operator_xonly_pk == the party that actually funded the payout" is not cryptographically enforced.

### Finding Description
`Operator::withdraw` builds the payout transaction with `create_payout_txhandler`, passing the calling operator's own key as `operator_xonly_pk`, which is written unsigned into an OP_RETURN output: [1](#0-0) 
The single witness placed on the transaction only covers the withdrawal input via the user's presigned signature: [2](#0-1) 
This signature is required to use `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params`: [3](#0-2) 
`SIGHASH_SINGLE` commits only to the output at the same index as the signed input (output 0 - the user's payout), and `ANYONECANPAY` means no other inputs are committed either. The OP_RETURN output (added at a later index) and any additional funding inputs bitcoind adds via `fund_raw_transaction` are therefore outside the scope of what the user's signature guarantees: [4](#0-3) 

Downstream, verifiers trust this OP_RETURN field as ground truth for "who paid": `update_finalized_payouts` parses it directly from the confirmed transaction and stores it as `payout_payer_operator_xonly_pk`, [5](#0-4) 
and `is_kickoff_malicious` compares a later kickoff's `operator_xonly_pk` against this stored value to decide whether a kickoff is honest or fraudulent: [6](#0-5) 

Because the withdrawal input signature (`in_signature`) becomes public once any operator broadcasts their payout transaction to the Bitcoin mempool, and because `SinglePlusAnyoneCanPay` explicitly allows reuse of the signed input/output pair inside an attacker-constructed transaction with arbitrary additional inputs and arbitrary other outputs, an unprivileged network observer can extract the signed input+output-0 pair from a pending/broadcast payout transaction and build a competing transaction that: (a) spends the same withdrawal UTXO, (b) preserves output 0 exactly (so the user's signature remains valid), and (c) substitutes a different, attacker-chosen value in the OP_RETURN output. If this competing transaction confirms instead of the original (a straightforward first-seen/fee race since both spend the same input), the DB and later fraud-detection logic (`is_kickoff_malicious`, `send_asserts`) will record an operator identity that never actually funded the payout, exactly mirroring the reported bug class where an unrelated balance/field is checked instead of the value/party actually involved in the transfer.

### Impact Explanation
This breaks the "operator credited versus the party that paid" binding called out as a Critical impact category. A forged OP_RETURN can misattribute a payout to an operator who never fronted the withdrawal. Concretely:
- An honest operator can be framed as having made a payout it never funded, which the protocol's fraud-detection (`is_kickoff_malicious`) treats as ground truth when validating that operator's *future* kickoffs for that same deposit, corrupting the reimbursement bookkeeping (`payout_payer_operator_xonly_pk`, `get_first_unhandled_payout_by_operator_xonly_pk`) that drives `PayoutCheckerTask` and `send_asserts`.
- Because the true funding source (whoever supplied the wallet inputs bitcoind added via `fund_raw_transaction`) is decoupled from the credited identity, this is a genuine misattributed-reimbursement / broken-attribution vulnerability rather than a benign implementation detail.

### Likelihood Explanation
Exploitation requires only: (1) visibility of a broadcast (unconfirmed) payout transaction in the public Bitcoin mempool — no privileged role, key, or insider access needed — and (2) the ability to construct and broadcast a standard alternative transaction reusing the exposed `SinglePlusAnyoneCanPay` signature, which is a well-known and easy Bitcoin transaction-construction technique. No verifier, operator, or aggregator collusion is required to mount the attack; the confirmed outcome only depends on ordinary mempool/mining race dynamics, which is within the threat model of an unprivileged attacker.

### Recommendation
Do not use an unauthenticated OP_RETURN as the sole record of payer identity. Options:
- Change the signed sighash scope for the withdrawal input to cover the OP_RETURN output as well (e.g., use `SIGHASH_ALL` for that input, or restructure the transaction so the operator-identity output is committed by a signature the operator itself controls and that also binds to the specific input set actually used).
- Alternatively, require that the OP_RETURN operator key be cryptographically tied to whichever input(s) actually funded the additional value (e.g., have the operator sign a commitment over the OP_RETURN content and require verifiers to validate that signature against the operator's known key before trusting `payout_payer_operator_xonly_pk`, rather than trusting any confirmed transaction's OP_RETURN unconditionally.

### Proof of Concept
1. Operator Y calls `withdraw`, producing a payout transaction `tx_Y` with: input = withdrawal UTXO (signed by user with `SinglePlusAnyoneCanPay`), output0 = user payout, output1 = anchor, output2 = OP_RETURN(Y's xonly_pk), plus additional funding inputs/change added by `fund_raw_transaction`/wallet signing, and broadcasts it to the network.
2. An attacker monitoring the mempool extracts the witness for input 0 (the user's `SinglePlusAnyoneCanPay` signature) from `tx_Y`.
3. The attacker constructs `tx_Attacker`: same input 0 (withdrawal UTXO) with the extracted witness reused, same output 0 (user payout, required to keep the signature valid), but with its own funding input(s) covering the fee/payout delta, and a new OP_RETURN embedding an arbitrary `operator_xonly_pk` (e.g. an honest operator Z who never participated).
4. The attacker broadcasts `tx_Attacker` with a higher fee or races propagation so it confirms instead of `tx_Y`.
5. `update_finalized_payouts` on all verifiers parses the confirmed `tx_Attacker`'s OP_RETURN and stores `payout_payer_operator_xonly_pk = Z`, even though Z never funded anything, corrupting `is_kickoff_malicious` and the payout-tracking DB state used for reimbursement decisions.

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

**File:** core/src/operator.rs (L614-674)
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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
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

**File:** core/src/verifier.rs (L2312-2328)
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
```
