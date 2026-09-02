## Title
Payout-attribution via unauthenticated OP_RETURN lets anyone assign an unwitting operator as "payer" without that operator funding the withdrawal — ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` attributes credit for fronting a withdrawal to whichever x-only pubkey appears in the payout transaction's OP_RETURN output, with no cryptographic binding between that pubkey and the party who actually supplied the transaction's extra inputs (the fronted BTC).

### Finding Description
The payout transaction is built by `create_payout_txhandler` with a `SinglePlusAnyoneCanPay` signature over input 0 only [1](#0-0) . That sighash type commits only to `txin[0]` and `txout[0]`; the anchor output and, critically, the OP_RETURN output carrying `operator_xonly_pk` are **not** covered by the signature.

The verifier later scans confirmed payout transactions and derives who gets credited as payer purely from that unauthenticated OP_RETURN field: [2](#0-1) 

This value is persisted as `payout_payer_operator_xonly_pk` and used to gate the reimbursement flow — the operator matching that pubkey is treated as "the party that paid" [3](#0-2)  and `validate_payer_is_operator` checks that the *caller's own* key equals this DB-recorded value before it will hand out reimbursement transactions [4](#0-3) .

Because only `txout[0]` (the user's payout) and `txin[0]` are signature-committed, anyone observing the mempool/broadcast payout tx can construct an alternate transaction that: keeps the same signed input/output-0 pair (so the schnorr verification in `operator.withdraw`/`aggregator.optimistic_payout`/mempool relay still passes), supplies their *own* additional funding inputs (instead of the legitimate fronting operator's), and sets an arbitrary xonly pubkey — e.g. a different, uninvolved operator's pubkey — in the OP_RETURN output. Once mined, `update_finalized_payouts` will record that arbitrary operator as the payer, even though that operator never funded anything.

### Impact Explanation
This breaks the intended equality "operator credited == operator that fronted the withdrawal." Consequences:
- An operator can be framed as the payer for a withdrawal it never funded. `validate_payer_is_operator` will match their own key, `PayoutCheckerTask`/`get_reimbursement_txs` flow will treat them as obligated to run the kickoff/reimbursement chain for a payout they did not actually front, potentially exposing them to disprove/challenge risk and to loss of collateral if they cannot produce a valid kickoff proving they paid (they can't, since they didn't).
- Conversely, the party that actually supplied the real fronting funds gets no attribution and can never claim reimbursement through the protocol's authorization path, since `get_reimbursement_txs`/`validate_payer_is_operator` only serves the DB-recorded (falsely attributed) operator.
- Net effect: the deposit's move-to-vault funds can become effectively stuck (the true payer cannot claim reimbursement, and the falsely-credited operator has no incentive/ability to run the kickoff for money it never disbursed), matching the "vault UTXO permanently frozen" / "honest operator permanently unable to be reimbursed" impact class.

### Likelihood Explanation
This requires only an unprivileged party able to observe/replace a payout transaction in the mempool before confirmation (a routine transaction-relay-level capability, not any protocol role), and does not require a verifier/operator/watchtower key, majority hashrate, or any privileged access. The only defense is confirmation speed, which is not a security guarantee.

### Recommendation
Do not derive payer attribution from an unauthenticated OP_RETURN field. Either sign the OP_RETURN output as part of the payout signature (e.g. use `SIGHASH_ALL` or otherwise commit all outputs including the OP_RETURN in the user's signed message), or independently prove that the additional funding inputs of the payout transaction actually belong to the operator being credited (e.g. verify input ownership/signatures against the operator's known collateral/wallet key) before writing `payout_payer_operator_xonly_pk`.

### Proof of Concept
1. Operator X calls `withdraw()`, producing a payout tx with input 0 signed `SinglePlusAnyoneCanPay`, output 0 = user payout, output 2 = OP_RETURN(X_pubkey), funded via `fund_raw_transaction` with X's wallet inputs [5](#0-4) .
2. Before this tx confirms, an attacker builds a replacement transaction reusing input 0 and output 0 unchanged (keeping the schnorr signature valid, since only these are committed under `SinglePlusAnyoneCanPay`), replaces the funding inputs with their own, and swaps the OP_RETURN to contain operator Y's pubkey instead of X's.
3. Attacker broadcasts/relays their version so it confirms first.
4. `update_finalized_payouts` parses the OP_RETURN and records `payout_payer_operator_xonly_pk = Y` [6](#0-5) .
5. Operator Y is now the only party authorized by `validate_payer_is_operator` to request reimbursement transactions [7](#0-6) , despite never funding the payout; the attacker who supplied the real funds has no path to reimbursement.

Note: I could not fully trace whether any downstream check (e.g., in circuit/bridge-circuit-host output verification) cross-validates the OP_RETURN pubkey against actual input ownership before circuit proving; this would need confirmation in a live/full-code session, as index size limits may have excluded some relevant proving-side code paths.

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

**File:** core/src/verifier.rs (L2311-2350)
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
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
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

**File:** core/src/operator.rs (L620-674)
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

**File:** core/src/operator.rs (L1686-1740)
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

        tracing::info!(
            "Payer xonly pk, payout blockhash and kickoff txid found and valid for own operator for the requested deposit id: {}, payer xonly pk: {:?}, payout blockhash: {:?}, kickoff txid: {:?}",
            deposit_id,
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid
        );

        Ok((payout_blockhash, kickoff_txid))
    }
```
