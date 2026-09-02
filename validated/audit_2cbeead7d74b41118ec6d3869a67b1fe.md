### Title
Unattributed replacement of an operator's in-flight `Payout` tx lets an unprivileged party mint a fake reimbursement credit for any operator - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The user's withdrawal authorization signature for the `payout_tx` is a `SinglePlusAnyoneCanPay` Taproot signature, which only commits to the spent input and to output index 0 (the user payout). It leaves the OP_RETURN output (index 2), which is the sole piece of on-chain data used to attribute "who fronted this withdrawal," completely unauthenticated and mutable by anyone who can see the signature.

### Finding Description
`create_payout_txhandler` builds the payout transaction with: input 0 = the user's withdrawal UTXO (spent with `SpendPath::KeySpend`), output 0 = user payout, output 1 = anchor, output 2 = an OP_RETURN containing `operator_xonly_pk` [1](#0-0) . The witness for input 0 is the user's `SinglePlusAnyoneCanPay` signature, and this sighash type is explicitly documented and enforced: [2](#0-1)  and verified in `Operator::withdraw`: [3](#0-2) .

`SIGHASH_SINGLE | ANYONECANPAY` only commits the signature to input 0 and to the output at the *same index* (output 0). It places **no constraint whatsoever** on outputs 1 and 2 (anchor and OP_RETURN). Any party who obtains this signature — which becomes public the instant any operator broadcasts (or even RBF-attempts) the payout tx, since it appears in the transaction's witness — can construct an entirely new transaction that:
- Reuses the same input and the same signature (still valid, since it doesn't cover the outputs being changed),
- Keeps output 0 byte-for-byte identical (required, since that is what the signature commits to),
- Funds the shortfall with the attacker's own BTC (via their own added inputs, exactly like `fund_raw_transaction` does for the legitimate operator) and pays a higher fee to win the mempool race / RBF replacement,
- Substitutes an **arbitrary** `operator_xonly_pk` into the OP_RETURN output — naming any operator the attacker chooses, including one that never touched this withdrawal.

Once this substitute transaction confirms, the verifier's Citrea/Bitcoin sync logic reads the OP_RETURN and blindly records whichever xonly-pk is present as the payer, with no verification that this key actually funded anything: [4](#0-3)  feeding into `update_payout_txs_and_payer_operator_xonly_pk` [5](#0-4) . The named operator's own `PayoutCheckerTask` then automatically discovers this "unhandled payout" attributed to itself and drives it through `handle_finalized_payout` and the kickoff/round/reimburse flow without any additional check that it, or anyone acting on its behalf, actually paid for the withdrawal: [6](#0-5) , `validate_payer_is_operator` only checks that the stored payer key equals the local operator's own key — it never checks that the operator's wallet/funds were the ones spent: [7](#0-6) .

This is the same class of bug as the referenced H-10 report: a value-movement side effect (idleETH decrement on `batchDepositETHForStaking`) is not mirrored by the corresponding credit-restoring effect (`bringUnusedETHBackIntoGiantPool` failing to increment `idleETH`), letting the accounting variable diverge from real custody and be exploited for profit. Here, the "credit" side (`payout_payer_operator_xonly_pk`) is derived from unauthenticated transaction data that is decoupled from "who actually paid" (whoever funded output 0's value beyond the tiny dust input), breaking the equality: `payout_payer_operator_xonly_pk == the entity that funded output 0`.

### Impact Explanation
This breaks the binding "the operator credited versus the party that paid." An operator can be credited with fronting a withdrawal it never funded and can then legitimately walk through the kickoff/reimburse process to withdraw `bridge_amount` from the corresponding `move_to_vault` UTXO — BTC leaves the vault without a matching, verifiably fronted withdrawal from that operator. This matches the Critical impact category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal."

### Likelihood Explanation
The attacker does not need to be an operator, verifier, watchtower, aggregator, or hold any privileged key. They only need to (a) observe a broadcast/mempool payout transaction (or otherwise learn the `SinglePlusAnyoneCanPay` signature, which is public once any payout tx is seen on the network), and (b) have enough BTC and fee budget to win a one-input replacement race, which is a purely economic/network condition, not a role or credential requirement. The victim operator's participation is entirely passive/automatic (its background `PayoutCheckerTask`/automation reacts to DB state populated from chain data). This is realistically exploitable whenever a payout transaction is visible before final confirmation.

### Recommendation
Bind the OP_RETURN (and ideally the anchor output) into the user's authorization so it cannot be altered by a third party — e.g., have the user sign with `SIGHASH_ALL` (or `SIGHASH_ALL|ANYONECANPAY` if additional operator-funded inputs must remain flexible) so all outputs, including the OP_RETURN attributing the fronting operator, are committed to by the signature. Alternatively, require the credited operator's own signature/commitment over the OP_RETURN payload (e.g., an operator-signed attestation) before recording `payout_payer_operator_xonly_pk`, rather than trusting unauthenticated on-chain OP_RETURN bytes as proof of payment.

### Proof of Concept
1. Operator A calls `withdraw()` for a legitimate withdrawal, producing and broadcasting `payout_tx` with input 0 = user's dust withdrawal UTXO, output 0 = user payout (signed `SinglePlusAnyoneCanPay`), output 1 = anchor, output 2 = OP_RETURN(A's xonly-pk) — see construction in [1](#0-0)  and broadcast/fund logic in [8](#0-7) .
2. Before `payout_tx` confirms, an unprivileged attacker (any Bitcoin network observer) extracts the txin witness signature, which is `SinglePlusAnyoneCanPay` and therefore only binds input 0 + output 0.
3. Attacker crafts `payout_tx'` reusing input 0 and the same signature, keeping output 0 identical, but replacing output 2 with OP_RETURN(operator B's xonly-pk) — an operator uninvolved in this withdrawal — and adds their own funding input(s) plus a higher fee to outbid `payout_tx` in the mempool/RBF.
4. `payout_tx'` confirms instead of `payout_tx`. The verifier's block sync (`update_finalized_payouts`) reads the OP_RETURN and records operator B as `payout_payer_operator_xonly_pk` [9](#0-8) .
5. Operator B's `PayoutCheckerTask` (running automation) automatically discovers this unhandled payout via `get_first_unhandled_payout_by_operator_xonly_pk` and drives the kickoff/round/reimburse flow to reclaim `bridge_amount` from the deposit's `move_to_vault` UTXO [10](#0-9) , even though B never funded any part of the withdrawal.

Note: I could not fully verify from the indexed snippets whether the `fund_raw_transaction`/TxSender RBF configuration (`replaceable` flag) as currently wired would in practice allow bitcoind/mempool policy to accept the attacker's competing transaction before confirmation (this affects only the timing/feasibility window, not the underlying signature-scope flaw). Confirming the exact RBF signaling and mempool acceptance behavior would require reviewing `crates/clementine-tx-sender/src/rbf.rs` in full, which was not retrieved in this session.

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

**File:** core/src/rpc/clementine.proto (L241-253)
```text
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
}
```

**File:** core/src/operator.rs (L628-674)
```rust
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

**File:** core/src/task/payout_checker.rs (L41-106)
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
