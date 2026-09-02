### Title
Payout transaction's operator-attribution OP_RETURN output is unsigned, allowing misattribution of reimbursement credit - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` builds the payout transaction with an OP_RETURN output that commits the fronting operator's x-only public key, but only the withdrawal input and the first (payout) output are covered by the user's `SIGHASH_SINGLE|ANYONECANPAY` signature. The OP_RETURN output (and the anchor output) are outside the signed digest and can therefore be swapped by anyone who observes the unconfirmed transaction, breaking the "operator credited == operator that actually fronted the payout" binding relied upon throughout the reimbursement pipeline.

### Finding Description
`withdraw()` in `core/src/operator.rs` builds a payout transaction via `create_payout_txhandler`, embedding the calling operator's own key into the OP_RETURN output: [1](#0-0) 

`create_payout_txhandler` places `operator_xonly_pk` into a bare OP_RETURN output, and only signs the withdrawal input and the payout output (`set_p2tr_key_spend_witness(&user_sig, 0)`), matching the sighash type documented as `SinglePlusAnyoneCanPay`: [2](#0-1) 

Because the user's signature uses `SIGHASH_SINGLE | ANYONECANPAY`, it only commits to input 0 and the output at the same index (output 0, the payout output). The OP_RETURN output (index 2) and the anchor output (index 1) are not covered by the signature, so anyone with visibility of the unconfirmed transaction (e.g., in the mempool, or via the RBF-funding flow in `fund_raw_transaction`) can re-serialize the transaction with a different OP_RETURN payload naming an arbitrary/different operator's x-only public key, while keeping the same signed input and payout output, and rebroadcast it.

Downstream, this OP_RETURN value is trusted as ground truth for "who paid":
- `update_finalized_payouts` parses the operator pubkey straight from the confirmed payout tx's OP_RETURN and stores it as `payout_payer_operator_xonly_pk`: [3](#0-2) 
- `validate_payer_is_operator` treats this stored value as authoritative when deciding whether the calling operator is entitled to reimbursement: [4](#0-3) 
- `is_kickoff_malicious` and `send_asserts` also gate correctness/kickoff behavior purely on this stored OP_RETURN-derived pubkey matching `kickoff_data.operator_xonly_pk`: [5](#0-4) [6](#0-5) 

This is the direct analog of the reported bug class: a value that is supposed to identify "the entity that performed the custody-relevant action" (mystery box owner after transfer / here, the operator that fronted the payout) is derived from mutable state that is never re-validated against the actual action, letting a state field diverge from the real party responsible.

### Impact Explanation
If an unprivileged attacker rewrites the OP_RETURN before confirmation to name a different operator's public key:
- The honest operator who actually funded/broadcast the payout can end up with `payout_payer_operator_xonly_pk` in the DB not matching its own key, so `validate_payer_is_operator` will reject it, permanently blocking that operator from claiming reimbursement for a payout it genuinely fronted.
- The named operator's own automated reimbursement flow (`get_reimbursement_txs` / kickoff automation, gated by `validate_payer_is_operator`, `is_kickoff_malicious`) will see a matching `payout_payer_operator_xonly_pk == self.signer.xonly_public_key` and proceed to attempt reimbursement for a payout it never made — i.e., an operator credited for a payout it never funded.

Both outcomes match the Critical impact categories in scope: "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation only requires observing/intercepting the payout transaction prior to confirmation (e.g., in the mempool) and rebroadcasting a variant with a modified OP_RETURN — an unprivileged, no-role-required action, since the OP_RETURN and anchor outputs are not covered by the user's `SIGHASH_SINGLE|ANYONECANPAY` signature. No compromise of any key, node, or role is required; only network visibility of the unconfirmed transaction and the ability to broadcast an alternative version of it.

### Recommendation
Bind the operator-attribution output to the signature. Options:
- Use a sighash type that commits to all outputs (e.g. `SIGHASH_ALL|ANYONECANPAY`) for the payout transaction so OP_RETURN/anchor cannot be altered post-signature, or
- Have the operator co-sign (or otherwise authenticate) the full transaction including the OP_RETURN payload, rather than relying solely on the unauthenticated OP_RETURN field parsed after confirmation.

### Proof of Concept
1. Operator A calls `withdraw()`, producing a payout tx with input 0 signed by the user (`SIGHASH_SINGLE|ANYONECANPAY`), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(Operator A's xonly pk). See [1](#0-0)  and [2](#0-1) .
2. Attacker observes this transaction in the mempool before confirmation.
3. Attacker constructs an alternate transaction reusing input 0 with its valid witness (unchanged, since only input 0 + output 0 are covered by the sighash), keeps output 0 identical, but replaces output 2's OP_RETURN payload with Operator B's x-only public key (and/or a different anchor output), then broadcasts/relays this version instead (or as a conflicting/replacing transaction depending on mempool policy).
4. If this variant confirms, `update_finalized_payouts` records `payout_payer_operator_xonly_pk = Operator B` for the withdrawal: [7](#0-6) .
5. Operator A's subsequent `get_reimbursement_txs`/kickoff flow fails `validate_payer_is_operator` (mismatch), permanently denying Operator A's reimbursement: [8](#0-7) . Meanwhile Operator B's automated kickoff/reimbursement pipeline sees a matching payer key and can proceed to attempt reimbursement for a payout it never made.

### Citations

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

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
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

**File:** core/src/verifier.rs (L2298-2342)
```rust
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
```
