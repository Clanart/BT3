### Title
Unauthenticated OP_RETURN Operator Attribution in `SinglePlusAnyoneCanPay`-Signed Payout Transactions Allows Front-Running That Permanently Freezes a Vault Deposit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The user's payout-authorization signature is required to use `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) . This sighash type only commits to the single withdrawal input and the output at the same index (the user's payout output); it leaves every other output — including the `OP_RETURN` output that records which operator "fronted" the withdrawal — completely unauthenticated [2](#0-1) . Because the withdrawal input is public once broadcast to the Bitcoin mempool, any unprivileged party can reuse that same signed input in a competing transaction that keeps the user's output identical (to stay valid under `SINGLE`) but supplies its own funding and a different/garbage/absent `OP_RETURN` value, and get it mined first via RBF/fee-bump. This breaks the binding "the operator credited (`OP_RETURN` pubkey) == the party that actually funded the payout," and can result in a deposit's move-to-vault UTXO becoming permanently unreimbursable.

### Finding Description
The payout transaction is built with a single Taproot key-spend input (the withdrawal UTXO) and three outputs: the user payout, an anchor, and an `OP_RETURN` carrying the operator's x-only pubkey [3](#0-2) . The witness signature supplied by the user only ever uses `SinglePlusAnyoneCanPay`, and this is strictly enforced server-side, converting even a default-sighash 64-byte signature into `SinglePlusAnyoneCanPay` [1](#0-0) . `SIGHASH_SINGLE|ANYONECANPAY` only binds the signer's input to the output at the same index (index 0, the user payout) — it does not commit to the anchor output or the `OP_RETURN` output at index 2.

When an operator locally builds and funds its payout transaction, it calls `fund_raw_transaction` to add its own inputs to cover the payout amount/fee [4](#0-3) ; those additional operator-owned inputs are signed with the wallet's default `SIGHASH_ALL`, which does commit to the `OP_RETURN`. However, before that transaction confirms, the withdrawal input's own signature (the only element that is validated on-chain from the user's side) is public in the mempool. Any observer can therefore construct a distinct, competing transaction that:
- Reuses the exact same signed withdrawal input (satisfies `SINGLE|ANYONECANPAY`, since it doesn't care what other inputs/outputs exist beyond output 0),
- Keeps output 0 byte-identical (required for `SINGLE` to remain valid),
- Supplies its own funding inputs (self-signed, so it can construct absolutely any other outputs),
- Sets the `OP_RETURN` to an operator pubkey that never fronted anything, to a nonexistent pubkey, or omits the `OP_RETURN` output entirely,

and gets this transaction mined first (double-spending the withdrawal UTXO before the legitimate operator's transaction confirms).

Downstream, the verifier's chain-sync logic parses the confirmed payout transaction's `OP_RETURN` to determine which operator gets credited with fronting the payout, defaulting to `None` if it's missing or malformed [5](#0-4)  and persists that value verbatim [6](#0-5) . The operator automation that detects "my payout to reimburse" strictly filters on an exact match of `payout_payer_operator_xonly_pk` [7](#0-6) ; a `NULL`/mismatched value will never match any operator's key in that `WHERE` clause, so `PayoutCheckerTask` never picks up the payout for any operator [8](#0-7) . Since the withdrawal UTXO is already spent, the legitimate operator can never submit its own correctly-attributed payout transaction for that withdrawal either, and no other operator can be reimbursed since the reimbursement circuit path (`is_kickoff_malicious`) requires the `OP_RETURN` operator to equal the kickoff operator [9](#0-8) . The deposit's move-to-vault funds therefore become permanently unclaimable.

### Impact Explanation
This directly matches the Critical impact category "a vault UTXO permanently frozen." No operator can ever be correctly reimbursed for the deposit tied to the hijacked withdrawal, because the on-chain payout transaction — the sole artifact both the automation (`PayoutCheckerTask`) and the anti-fraud check (`is_kickoff_malicious`) rely on — carries no operator attribution that any real, correctly-provisioned operator can match.

### Likelihood Explanation
The withdrawal input's signature is designed to be shared with (potentially all) operators and, once any operator broadcasts a payout attempt, is visible to the entire Bitcoin network in the mempool before confirmation. Reusing a `SIGHASH_SINGLE|ANYONECANPAY`-signed input in a new transaction is a standard, well-known Bitcoin technique requiring no special protocol privileges — only the ability to construct and fee-bump a Bitcoin transaction and fund the payout output itself. No verifier, operator, watchtower, or other privileged role is required to be the attacker.

### Recommendation
Require the withdrawal-authorization signature to use `SIGHASH_ALL` (or `AllPlusAnyoneCanPay`) so that it commits to every output of the payout transaction, including the `OP_RETURN` operator-attribution output, preventing any party from altering the attribution or transaction shape without invalidating the user's signature.

### Proof of Concept
1. An operator (or the aggregator) obtains a user's `SinglePlusAnyoneCanPay` signature for a withdrawal and broadcasts a payout transaction spending the withdrawal UTXO, with output 0 = the user's payout and output 2 = its own `OP_RETURN` attribution, per `create_payout_txhandler

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/operator.rs (L620-673)
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

**File:** core/src/database/verifier.rs (L282-298)
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
```

**File:** core/src/task/payout_checker.rs (L39-51)
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
```
