## Title
Payout attribution (`OP_RETURN` operator identity) is not covered by the user's withdrawal signature, allowing anyone to hijack operator reimbursement credit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The payout transaction's user-authorizing signature is a `SinglePlusAnyoneCanPay` Taproot signature, which under BIP341 rules commits only to the single signed input and to the output at the *same index* as that input (index 0). The `create_payout_txhandler` function places the operator-attribution `OP_RETURN` output at index 2 and the CPFP anchor at index 1 — both outside the signature's commitment. Any party who observes the broadcast (but unconfirmed) payout transaction can therefore rebuild an alternative transaction that reuses the exact same signed input/signature, replaces the funding inputs/change, and substitutes an arbitrary operator's x-only public key into the `OP_RETURN`, all while still delivering the exact contractually-required payment to the user. This breaks the binding "operator credited (`payout_payer_operator_xonly_pk`) == operator that actually paid," which is the same class of bug as the reported `transferFrom()` issue: an action performed by one party (`msg.sender`/the fronting payer) is not authenticated against the identity that downstream logic trusts and rewards.

### Finding Description
`create_payout_txhandler` builds the payout transaction with: [1](#0-0) 

The `SinglePlusAnyoneCanPay` sighash type is documented explicitly in the proto and enforced during verification in `withdraw()`: [2](#0-1) [3](#0-2) 

Under `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`, the user's signature commits only to input 0 and to output 0 (the user's own payout output). It does **not** cover the anchor output (index 1) or the `OP_RETURN` output (index 2) that records which operator "fronted" the withdrawal. Nothing else in the protocol binds the `OP_RETURN` content to the signature or to the party who actually supplies the funding UTXOs added by `fund_raw_transaction` (`add_inputs: true`): [4](#0-3) 

Downstream, verifiers scan confirmed blocks and extract the payer identity purely from this unauthenticated `OP_RETURN`, storing it unconditionally as the "payer": [5](#0-4) 

That stored `payout_payer_operator_xonly_pk` is later trusted by the credited operator's own reimbursement flow with no cross-check against who actually funded the payout inputs: [6](#0-5) [7](#0-6) 

Because the fee/change inputs and the `OP_RETURN` are outside the signed message, an unprivileged party who sees the pending payout transaction (public once broadcast to the mempool, prior to confirmation) can construct a competing, still-validly-signed transaction that pays the user the exact required amount (satisfying `SIGHASH_SINGLE`) but attributes the payout to a different operator's `xonly_pk` than the one who actually supplied the funding inputs.

### Impact Explanation
This breaks the "operator credited vs. party that paid" binding required by the bridge's custody model. A party who never funded the withdrawal can force the protocol to record a chosen operator as the payer. That operator's own automation (`get_first_unhandled_payout_by_operator_xonly_pk` → `validate_payer_is_operator` → kickoff/reimburse flow) will then treat the falsely-attributed payout as legitimate and proceed to claim BTC reimbursement from the round/collateral chain for a withdrawal it never actually funded — matching the Critical category "an operator reimbursed for a payout it never funded." It can also be used to race out and deny the legitimate fronting operator its credit, or to grief an uninvolved operator by forcing its automation into unexpected kickoff/challenge exposure it did not initiate.

### Likelihood Explanation
No privileged role is required — only visibility into the Bitcoin mempool/network, which is inherently public once any operator broadcasts the payout transaction. The attacker needs only to notice an unconfirmed payout transaction, extract the reusable `SinglePlusAnyoneCanPay` signature and input, and race a substitute transaction (with a different fee-paying input set and `OP_RETURN`) to confirmation ahead of the original. This is a standard SIGHASH_SINGLE|ANYONECANPAY transaction-malleability/front-running technique, not requiring any key compromise or insider role.

### Recommendation
Bind the operator attribution to the signature commitment. For example, either sign the full transaction (or at least commit to the `OP_RETURN`/anchor outputs) with an additional signature/covenant that the credited operator itself must produce, or use `SIGHASH_ALL`-covered outputs for anything used to attribute reimbursement credit, and cross-check the actual funding inputs of the confirmed payout transaction against the claimed operator before recording `payout_payer_operator_xonly_pk`.

### Proof of Concept
1. Operator A calls `withdraw()`, producing and broadcasting a payout transaction: input = withdrawal UTXO (user-signed with `SinglePlusAnyoneCanPay`), output 0 = user payout, output 1 = anchor, output 2 = `OP_RETURN(A_xonly_pk)`, funded via `fund_raw_transaction` with Operator A's wallet inputs.
2. Before confirmation, an observer extracts the signed input (`txin` + witness) from the mempool transaction.
3. The observer builds a new transaction reusing that same signed input/signature, keeping output 0 identical (required by `SIGHASH_SINGLE`), but supplying their own funding inputs/change and setting `OP_RETURN` to Operator B's `xonly_pk` (or any other target). They then get this transaction confirmed instead (e.g. by paying a higher fee to win the mempool race).
4. `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) parses the confirmed transaction and records `payout_payer_operator_xonly_pk = B`.
5. Operator B's automation (`validate_payer_is_operator` in `core/src/operator.rs:1686-1740`) sees itself as the recorded payer and proceeds through the normal kickoff/reimburse flow, claiming BTC reimbursement for a withdrawal it never funded.

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

**File:** core/src/rpc/clementine.rs (L239-253)
```rust
pub struct WithdrawParams {
    /// The ID of the withdrawal in Citrea
    #[prost(uint32, tag = "1")]
    pub withdrawal_id: u32,
    /// User's \[`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`\]
    /// signature
    #[prost(bytes = "vec", tag = "2")]
    pub input_signature: ::prost::alloc::vec::Vec<u8>,
    /// User's UTXO to claim the deposit
    #[prost(message, optional, tag = "3")]
    pub input_outpoint: ::core::option::Option<Outpoint>,
    /// The withdrawal output's script_pubkey (user's signature is only valid for
    /// this pubkey)
    #[prost(bytes = "vec", tag = "4")]
    pub output_script_pubkey: ::prost::alloc::vec::Vec<u8>,
```

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

**File:** core/src/operator.rs (L639-675)
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
