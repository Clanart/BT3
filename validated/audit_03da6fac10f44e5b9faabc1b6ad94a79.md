### Title
Payout attribution via unauthenticated, unsigned OP_RETURN allows misattribution of withdrawal-fronting credit — ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx` construction embeds the operator's x-only pubkey in an OP_RETURN output that is not covered by the user's signature (SIGHASH_SINGLE|ANYONECANPAY), yet this OP_RETURN is the *sole* source of truth used later on-chain to determine which operator "fronted" a withdrawal and is therefore entitled to bridge reimbursement.

### Finding Description
`create_payout_txhandler` builds a payout transaction with three outputs: the user's payout output (index 0), an anchor output (index 1), and an OP_RETURN output (index 2) containing `operator_xonly_pk`, and signs only input 0 with the user's signature using `SinglePlusAnyoneCanPay` sighash [1](#0-0) . SIGHASH_SINGLE binds only the input being signed to the output at the *same index* (index 0); it does not cover outputs 1 or 2, and ANYONECANPAY does not restrict what other inputs may be added. The comment in `operator.rs::withdraw` explicitly documents this: "Ensure the signature uses SinglePlusAnyoneCanPay sighash type" [2](#0-1) .

Once broadcast/relayed, the OP_RETURN content is later read back and trusted verbatim by `update_finalized_payouts`, which extracts the pubkey from the OP_RETURN of the confirmed transaction and writes it into `payout_payer_operator_xonly_pk` in the `withdrawals` table via `update_payout_txs_and_payer_operator_xonly_pk` [3](#0-2) [4](#0-3) . There is no cryptographic binding proving that the entity whose pubkey appears in the OP_RETURN actually supplied the funding inputs that paid the user.

This attribution then drives two critical downstream flows:
1. `PayoutCheckerTask::run_once` polls `get_first_unhandled_payout_by_operator_xonly_pk` for the *node's own* xonly pubkey and, if a match is found, automatically proceeds to call `handle_finalized_payout` and begins the kickoff/reimbursement process for that withdrawal [5](#0-4) .
2. `validate_payer_is_operator` / `get_reimbursement_txs` later checks only that `payer_xonly_pk == self.signer.xonly_public_key` before releasing the reimbursement transaction flow [6](#0-5) .

Because the OP_RETURN is unsigned/unauthenticated, anyone who observes an in-flight (unconfirmed) payout transaction can malleate it: strip the funding inputs and outputs beyond index 0 (which remain unsigned) and replace the OP_RETURN pubkey with an arbitrary operator's x-only pubkey, then get their variant confirmed instead. The equality the system relies on — "operator credited as payer" == "operator that actually funded the withdrawal" — can be broken without any privileged role, key compromise, or majority hashrate; it only requires ordinary mempool visibility and enough BTC to fund an equivalent transaction.

### Impact Explanation
This breaks the custody/attribution binding "the operator credited" vs "the party that paid" explicitly called out in scope. The consequence matches the Critical category "an operator reimbursed for a payout it never funded": a party can cause the `withdrawals.payout_payer_operator_xonly_pk` field to point to an operator that did not supply the withdrawal funds. That operator's own automation (`PayoutCheckerTask`) will then autonomously kick off the on-chain BitVM reimbursement flow and eventually claim the `Reimburse` transaction paying out the fixed bridge amount from the round/kickoff outputs, without that operator having spent any of its own capital to front the withdrawal. Conversely, an operator who legitimately funded the withdrawal but loses the malleation race is left with no attribution and cannot be reimbursed for a payout it did fund — an "honest operator permanently unable to be reimbursed," another explicitly listed Critical impact.

### Likelihood Explanation
Exploitation only requires visibility into the mempool (public, unauthenticated) and the ability to fund one's own version of the transaction; no signature forgery, node key, or privileged role is required, since SIGHASH_SINGLE|ANYONECANPAY leaves outputs 1/2 and any additional funding inputs completely unauthenticated. The main constraint is that the attacker must be able to fund the replacement transaction with the withdrawal amount plus fees and win the propagation/confirmation race — feasible for any Bitcoin-fee-competitive actor, and trivially reproducible in a local regtest by capturing the unconfirmed `payout_tx` and rebuilding it with a substituted OP_RETURN output and one's own funding UTXO before the original is mined.

### Recommendation
Cryptographically bind the operator identity to the payout transaction instead of trusting an unsigned OP_RETURN:
- Sign the payout transaction (or at minimum the OP_RETURN output and the funding inputs) with a signature verifiable against the claimed operator's key (e.g., use `SIGHASH_ALL` for at least the operator-funded inputs/outputs, or have the operator co-sign a commitment covering the OP_RETURN content), so `update_finalized_payouts` can validate that the operator named in OP_RETURN actually authorized/funded the transaction.
- Alternatively, require the aggregator/verifiers to countersign or attest the withdrawal-to-operator assignment before it is trusted for reimbursement, rather than deriving attribution purely from mutable transaction data observed on-chain.

### Proof of Concept
1. Operator A calls `withdraw` with a valid `in_signature` (SinglePlusAnyoneCanPay), builds and funds `payout_tx` via `fund_raw_transaction` (adds A's own inputs, sets `change_position: 1`) as in `Operator::withdraw` [7](#0-6) , and broadcasts it.
2. Before confirmation, an observer copies the transaction, keeps input 0 and output 0 (user's UTXO/output, protected by the SIGHASH_SINGLE signature), but replaces: the OP_RETURN output (index 2) with their own/arbitrary operator's xonly pubkey, and the fee-funding inputs with their own UTXOs of sufficient value; then broadcasts this variant with a higher fee (RBF) so it confirms instead of A's version.
3. When the transaction confirms, `update_finalized_payouts` reads the OP_RETURN from the confirmed tx and calls `update_payout_txs_and_payer_operator_xonly_pk` with the substituted pubkey [8](#0-7) .
4. `PayoutCheckerTask` running for the substituted operator's node observes an "unhandled payout" credited to itself and begins the reimbursement flow for a withdrawal it never funded [5](#0-4) , while operator A — the true funder — has no path to reimbursement since the DB attribution no longer names A.

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

**File:** core/src/operator.rs (L1705-1719)
```rust
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

**File:** core/src/verifier.rs (L2298-2350)
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
