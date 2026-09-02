### Title
Payout OP_RETURN operator attribution is not covered by the withdrawal signature, allowing an unprivileged party to hijack or nullify operator reimbursement credit - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` signs the payout transaction with `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`, covering only the withdrawal input and the single user-payout output. The OP_RETURN output that records *which operator fronted the withdrawal* — the field the entire reimbursement pipeline keys on — is left completely outside the signed message, as are any funding inputs added later. Anyone who can reconstruct the transaction (e.g. by observing it in the mempool) can therefore rebroadcast a competing, still-valid transaction that spends the same withdrawal UTXO, keeps the mandated user output, but swaps in arbitrary OP_RETURN data and their own funding inputs — deciding unilaterally who gets credited as "the operator who paid," or making sure nobody is credited at all.

### Finding Description
`create_payout_txhandler` builds the payout tx with a single input (the user's withdrawal UTXO, signed by the user off-chain) and three outputs: the user payout (index 0), an anchor (index 1), and an OP_RETURN carrying the fronting operator's x-only pubkey (index 2): [1](#0-0) 

The user's signature is `SinglePlusAnyoneCanPay`, verified only against `sighash_txin(0, ...)` in `Operator::withdraw`: [2](#0-1) 

Under BIP341/BIP143 semantics, `SIGHASH_SINGLE` binds the signature only to the single input and the single output at the same index (output 0); `SIGHASH_ANYONECANPAY` additionally excludes all other inputs from commitment. That means outputs 1 (anchor) and 2 (OP_RETURN operator attribution), and any additional funding inputs the operator adds via `fund_raw_transaction`, are **not part of what the user signed**: [3](#0-2) 

The operator attribution recorded in OP_RETURN is trusted at face value later by the block syncer, which parses the *mined* transaction and writes it into `withdrawals.payout_payer_operator_xonly_pk`, defaulting to `NULL` if the OP_RETURN is missing or malformed: [4](#0-3) 

Everything downstream — the "unhandled payout" lookup that drives each operator's automated reimbursement task, and the malicious-kickoff check that gates who is allowed to spend the round/kickoff path — keys strictly on this unauthenticated field: [5](#0-4) [6](#0-5) 

Because the OP_RETURN (and any extra funding inputs) sit entirely outside the signed message, anyone who observes a broadcast (but unconfirmed) payout tx in the mempool — or who otherwise learns the ANYONECANPAY-signed input+output pair — can construct a conflicting double-spend of the same withdrawal UTXO that:
- reuses the identical signed input and output 0 (so the user's signature remains valid),
- supplies their **own** funding input(s) to cover the payout amount (permitted, since ANYONECANPAY allows arbitrary extra inputs),
- replaces the OP_RETURN with (a) an arbitrary/invalid payload, or (b) any other operator's x-only pubkey,
and gets it mined instead of the honest operator's original transaction (a plain first-confirmed-wins race on the shared UTXO).

This breaks the intended equality the protocol depends on: `payout_payer_operator_xonly_pk == the party that actually fronted BTC to the user`. The attacker, not any operator, controls which side of that equality is written to the database.

### Impact Explanation
Two concrete outcomes follow directly from this:

1. **Vault UTXO permanently frozen (Critical).** If the attacker's replacement transaction carries no valid OP_RETURN pubkey, `payout_payer_operator_xonly_pk` is stored as `NULL`. `get_first_unhandled_payout_by_operator_xonly_pk` filters strictly on `payout_payer_operator_xonly_pk = $1` for a *specific* operator's own key, so no operator's automated flow (`PayoutCheckerTask` / `get_reimbursement_txs`) will ever pick this withdrawal up. Since the Reimburse transaction path requires this DB-recorded operator identity to drive the kickoff/round flow, the deposit's `MoveToVaultTx` output backing this withdrawal can never be spent through the normal reimbursement path — the bridge amount is stuck permanently, even though the user was correctly paid.

2. **Operator credited for a payout it never funded (Critical).** If the attacker instead inserts a real, arbitrary operator's x-only pubkey into OP_RETURN while funding the output with their own BTC, that operator's automated pipeline sees an "unhandled payout" attributed to itself and proceeds through kickoff/round/reimburse to claim the deposit's `bridge_amount` from the vault — despite never having fronted the withdrawal. `is_kickoff_malicious` will not flag this as malicious because the OP_RETURN pubkey matches the kickoff sender's pubkey by construction. This directly breaks the "operator credited versus the party that paid" custody binding: BTC leaves the move-to-vault UTXO to reimburse an operator for a withdrawal someone else funded.

Both outcomes require no privileged role, key, or node compromise — only observing an unconfirmed transaction in the mempool (or otherwise obtaining the ANYONECANPAY-signed input/output pair) and broadcasting a self-funded competing transaction.

### Likelihood Explanation
The precondition is simply that an honest operator's payout transaction is visible unconfirmed (standard mempool visibility) before it confirms, or that the "off-chain" user signature otherwise leaks — both are realistic given normal Bitcoin propagation and multi-operator withdrawal flows where several operators race to service the same withdrawal request. No special privileges, keys, or roles are needed by the attacker; only Bitcoin funds to cover the payout amount (recoverable in the "swap to arbitrary operator" variant, since the attacker still gets an unaffected output) or, in the freeze variant, no funds requirement is even needed for it to be economically damaging to the protocol.

### Recommendation
Bind the operator-attribution OP_RETURN output (and ideally the funding inputs) into the value the user actually signs, e.g. by having the user's signature use `SIGHASH_ALL` (or a taproot annex/extra commitment) that covers all outputs of the payout transaction, or by having the withdrawal registration on Citrea itself commit to the specific operator's pubkey up front (so the OP_RETURN must match a value that was already fixed before any operator could front the payout, and mismatches are provably invalid, not merely "no operator credited"). At minimum, treat a non-matching or missing OP_RETURN as cause for the deposit to fall back to a manual/dispute recovery path instead of silently orphaning the vault UTXO forever, and require the party spending the withdrawal UTXO to prove it is the operator identified in the same signed message.

### Proof of Concept
1. Operator O funds a legitimate `withdraw()` request for withdrawal index `w`: it builds `payout_txhandler` (input = withdrawal UTXO, output 0 = user payout, output 2 = OP_RETURN with O's xonly pk), funds it via `fund_raw_transaction`, signs, and broadcasts it to the Bitcoin mempool [7](#0-6) .
2. Before this transaction confirms, an observer extracts the mempool transaction and copies the single signed input (`previous_output` + witness, valid due to `SinglePlusAnyoneCanPay`) and output 0 (must be preserved to satisfy `SIGHASH_SINGLE`).
3. The observer constructs a new transaction using the same input+output0, adds their own UTXO(s) as additional inputs to cover the fee/output funding (allowed by `ANYONECANPAY`), and sets the OP_RETURN output to either garbage bytes or another operator P's xonly pubkey, per `create_payout_txhandler`'s output layout [8](#0-7) .
4. The observer broadcasts this transaction with a higher fee so it confirms first, causing O's original transaction to be rejected as a double-spend.
5. The block syncer records `payout_payer_operator_xonly_pk` from the confirmed transaction's OP_RETURN — either `NULL` (freezing the vault UTXO forever, since no operator's `get_first_unhandled_payout_by_operator_xonly_pk` will ever match) or operator P's pubkey (causing P, who never funded the withdrawal, to be credited and eventually reimbursed from the vault) [4](#0-3) .

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

**File:** core/src/operator.rs (L620-691)
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

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;

        Ok(signed_tx)
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
