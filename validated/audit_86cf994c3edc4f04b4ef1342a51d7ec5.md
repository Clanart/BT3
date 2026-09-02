This confirms the analysis: `withdrawal_utxo` is fixed by Citrea's withdrawal record (`update_withdrawal_utxo_from_citrea_withdrawal`), and `create_optimistic_payout_txhandler` requires a `user_sig` (the withdrawal UTXO owner's own Schnorr key) as key-spend witness on input 0, matching `withdrawal_prevout.script_pubkey`'s taproot pubkey [1](#0-0) . The `input_signature` must verify against that same UTXO's owning key [2](#0-1) , meaning only the person who controls the withdrawal UTXO's private key (i.e., the withdrawer themselves) can produce a valid `input_signature`/`output_txout` pair for either request. Verifiers' role is purely to co-sign as the N-of-N counterparty on input 1 (the move-to-vault UTXO) [3](#0-2) .

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L459-491)
```rust
pub fn create_optimistic_payout_txhandler(
    deposit_data: &mut DepositData,
    input_utxo: UTXO,
    output_txout: TxOut,
    user_sig: taproot::Signature,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    let move_txhandler: TxHandler = create_move_to_vault_txhandler(deposit_data, paramset)?;
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::NotStored,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::non_ephemeral_anchor_output(),
        ))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    Ok(txhandler)
```

**File:** core/src/verifier.rs (L1580-1586)
```rust
    ) -> Result<PartialSignature, BridgeError> {
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self.rpc.is_utxo_spent(&input_outpoint).await? {
            return Err(
                eyre::eyre!("Withdrawal utxo {:?} is already spent", input_outpoint).into(),
            );
        }
```

**File:** core/src/verifier.rs (L1691-1712)
```rust
        let opt_payout_secnonce = {
            let mut session_map = self.nonces.lock().await;
            let session = session_map
                .sessions
                .get_mut(&nonce_session_id)
                .ok_or_else(|| eyre::eyre!("Could not find session id {nonce_session_id}"))?;
            session
                .nonces
                .pop()
                .ok_or_eyre("No move tx secnonce in session")?
        };

        let opt_payout_partial_sig = musig2::partial_sign(
            deposit_data.get_verifiers(),
            None,
            opt_payout_secnonce,
            agg_nonce,
            self.signer.keypair,
            Message::from_digest(sighash.to_byte_array()),
        )?;

        Ok(opt_payout_partial_sig)
```
