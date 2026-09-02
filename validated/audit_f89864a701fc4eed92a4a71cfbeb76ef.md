### Title
Honest operator's kickoff-based payout can be silently downgraded to "unattributed/optimistic" by any withdrawer stripping the OP_RETURN via SIGHASH_SINGLE|ANYONECANPAY output malleability - ([File: core/src/verifier.rs])

### Summary
The withdrawer's `input_signature` for a payout is required to use `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) , which under BIP341 only commits to input 0 and the single output at the same index (output 0). It leaves the anchor output and the operator's OP_RETURN output completely unauthenticated. Since the withdrawer is the one who creates this signature (via the `withdraw` call on Citrea) before any operator ever sees it, the withdrawer can independently build and race a payout transaction that reuses the same input+output-0 pair but omits the OP_RETURN (and the operator-added fee inputs/anchor), and get that transaction confirmed instead of the honest operator's transaction. `Verifier::update_finalized_payouts` then records `operator_xonly_pk = NULL` for that withdrawal because it only tracks "whatever spent the UTXO on-chain" without any check that it is a real optimistic (N-of-N-signed) payout.

### Finding Description
Binding claimed to hold: `withdrawals.payout_payer_operator_xonly_pk IS NULL` ⇔ withdrawal was serviced by `create_optimistic_payout_txhandler` (N-of-N verifier signed). This binding is broken.

Code path:
1. `get_payout_txs_for_withdrawal_utxos` picks the txid of *whatever transaction spent* `withdrawal_utxo_txid:withdrawal_utxo_vout` on-chain, with no reference to which specific transaction (operator payout vs. optimistic payout vs. anything else) was expected [2](#0-1) .
2. `Verifier::update_finalized_payouts` takes that confirmed spending tx, tries to parse an OP_RETURN xonly-pubkey from it, and if it fails, sets `operator_xonly_pk = None` while only logging a warning that this "can happen if optimistic payout is used, or an operator constructed the payout tx wrong" [3](#0-2) . There is no cross-check against the deterministic txid of the actual `create_optimistic_payout_txhandler` transaction (which is fully known: it spends `input_outpoint` + `MoveToVault` output, and is N-of-N MuSig2-signed) [4](#0-3) .
3. `create_payout_txhandler` (the honest operator-fronted tx) puts the operator xonly pk into a *separate, uncommitted* OP_RETURN output [5](#0-4) . The user's signature only covers input 0 + output 0 because `parse_withdrawal_sig_params` mandates `SinglePlusAnyoneCanPay` [6](#0-5) ; the operator verifies this same sighash before broadcasting [7](#0-6) . The anchor and OP_RETURN outputs, and any operator-added fee inputs from `fund_raw_transaction`, are never bound by the user's signature.
4. Because the withdrawer/attacker created this signature themselves (it is a value they choose per the threat model), they can, before or in a race with the honest operator's broadcast, build their own transaction spending the same withdrawal UTXO with the same input+output0 (satisfying the signature), their own fee inputs, and no OP_RETURN/anchor at all. If this transaction confirms instead of (or in place of, via RBF/first-seen race) the operator's transaction, the chain now shows a payout with no attributable operator pk.
5. `is_kickoff_malicious` reads `payout_info` and, seeing `operator_xonly_pk_opt == None`, unconditionally treats the honest operator's later, legitimate kickoff as malicious ("No operator xonly pk found in payout tx OP_RETURN, assuming malicious") [8](#0-7) . This triggers a Challenge against the honest operator [9](#0-8) .
6. The honest operator cannot exculpate itself in the disprove/bridge circuit either, because that circuit also unconditionally expects an OP_RETURN in the payout transaction to recover the operator xonly pk for the deposit-constant check ("Payout transaction must have an OP_RETURN output") [10](#0-9) . With no OP_RETURN on the confirmed payout tx, the honest operator's kickoff/proof path is permanently unattributable, and `get_first_unhandled_payout_by_operator_xonly_pk` will never find this withdrawal under the operator's key either (it filters strictly on `payout_payer_operator_xonly_pk = $1`) [11](#0-10) .

None of the listed guards prevent this: `SECP.verify_schnorr` correctly validates a signature that legitimately only commits to input0+output0 under the sighash type the protocol itself mandates; `is_deposit_valid`/`is_profitable` operate earlier and don't re-check which tx eventually confirms; there is no uniqueness/consistency constraint tying `payout_txid` to a specific expected transaction template.

### Impact Explanation
An honest operator who correctly fronts a withdrawal permanently loses Reimburse eligibility for that fronted payout (their `withdrawals.payout_payer_operator_xonly_pk` becomes NULL, matching category "an honest operator permanently unable to be reimbursed"), and their subsequent, entirely correct kickoff is classified malicious, exposing them to a Challenge and — because the bridge circuit itself requires an OP_RETURN to reconstruct the deposit constant for disprove — the operator cannot produce a valid proof to defend the kickoff, leading to loss of collateral ("an honest operator's collateral burned"). The attack is repeatable per-withdrawal/per-deposit: any withdrawer (i.e., any unprivileged user who calls `withdraw` on the Citrea bridge) can perform it against any operator servicing their withdrawal, without needing any operator or verifier key material.

### Likelihood Explanation
The attacker only needs to be the withdrawing user (fully permitted by the threat model: they choose the withdrawal UTXO, its signature, and sighash flag, and call `withdraw` on Citrea). The required sighash type (`SinglePlusAnyoneCanPay`) is mandatory protocol behavior, not an attacker-induced weakening. Winning the race against the operator's broadcast requires only standard mempool/fee competition (or RBF if the operator's funding transaction is replaceable), which is well within reach of a determined withdrawer paying normal Bitcoin fees. No mainnet or live Citrea dependency is needed to demonstrate the DB-level misclassification; it can be reproduced fully on regtest with a `MockCitreaClient`.

### Recommendation
Do not infer "optimistic payout" purely from the absence of a parseable OP_RETURN. Instead, `update_finalized_payouts` should positively verify that a `None`-OP_RETURN payout tx matches the deterministic `create_optimistic_payout_txhandler` transaction template (e.g., same txid as computed by reconstructing the optimistic payout tx for that deposit, or checking that it spends the `MoveToVault` output as its second input and carries a valid aggregated N-of-N script-path signature) before writing `NULL` into `payout_payer_operator_xonly_pk`. Any payout spending only the withdrawal UTXO (not the move-to-vault UTXO) that lacks a valid operator OP_RETURN should be flagged as an anomalous/unattributed payout requiring separate (non-punitive to any single operator) handling, rather than silently equated with the optimistic path.

### Proof of Concept
`cargo test` plan (regtest, `MockCitreaClient`, no mainnet/live Citrea):
1. Run a normal deposit via `run_single_deposit`, then register a withdrawal UTXO controlled by a test-owned key, sign it with `TapSighashType::SinglePlusAnyoneCanPay` (as `generate_withdrawal_transaction_and_signature` already does).
2. Have operator0 call `withdraw`/`internal_withdraw`, capturing its constructed `create_payout_txhandler` transaction (with OP_RETURN) but do not let it confirm.
3. Independently construct a second transaction (as the "attacker"/withdrawer) that spends the same withdrawal UTXO with the same witness (input0), the identical output 0, but funds its own fee inputs and omits the OP_RETURN and anchor outputs; broadcast this instead (e.g., with a higher fee) and mine it so it confirms in place of the operator's tx.
4. Run the finalized-payout sync (`update_finalized_payouts`) and assert: `db.get_payout_info_from_move_txid(...).0 == None` — i.e., indistinguishable at the DB level from a genuine `optimistic_payout` row (compare against a row produced by an actual `create_optimistic_payout_txhandler` flow, asserting both have `operator_xonly_pk == None`).
5. Have the honest operator0 subsequently send its legitimate kickoff for that deposit and assert that `Verifier::is_kickoff_malicious` returns `true` (via the `"No operator xonly pk found ... assuming malicious"` path), and that a `Challenge` tx is queued against the honest operator, demonstrating the loss of Reimburse eligibility and exposure to collateral loss.

### Citations

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

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

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
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

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L1987-2017)
```rust
        if is_malicious {
            tracing::warn!(
                "Malicious {} detected. {} Challenge tx: {} for deposit {}",
                kickoff_data,
                match challenged_before {
                    false => "This is the first malicious kickoff in the current round.",
                    true => "This is not the first malicious kickoff in the current round.",
                },
                bitcoin::consensus::encode::serialize_hex(&challenge_tx),
                deposit_outpoint
            );
            // do not automatically send challenge txs on mainnet or testnet4
            if !challenged_before
                && !matches!(
                    self.config.protocol_paramset().network,
                    bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
                )
            {
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L459-492)
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
}
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");
```
