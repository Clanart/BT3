### Title
Payout transaction's operator-attribution OP_RETURN is unsigned, letting an unprivileged party hijack payer credit for a withdrawal - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The `payout_tx` built by `create_payout_txhandler` commits the "operator who fronted this pegout" solely via an unsigned `OP_RETURN` output. The only signature present on the transaction (the user's `SinglePlusAnyoneCanPay` signature) covers input 0 and output 0 exclusively, leaving the `OP_RETURN` output completely unauthenticated. Verifiers (`update_finalized_payouts`) trust this `OP_RETURN` as ground truth for "who paid the withdrawal," exactly the same class of bug as H-01: an unauthenticated/incorrect identifier is used to build state that other components (validators/verifiers) act upon.

### Finding Description
`create_payout_txhandler` builds the payout transaction with:
- input 0: the withdrawal UTXO, spent with the user's `SinglePlusAnyoneCanPay` signature
- output 0: the user's payout
- output 1: anchor
- output 2: `OP_RETURN` containing the paying `operator_xonly_pk` [1](#0-0) 

The user's signature is `SIGHASH_SINGLE | ANYONECANPAY`, which — per protocol design and the operator's own verification code — only binds input 0 and the corresponding output 0: [2](#0-1) 

Because `ANYONECANPAY` allows arbitrary additional inputs/outputs and `SIGHASH_SINGLE` only binds output index 0, the `OP_RETURN` output (index 2, holding `operator_xonly_pk`) is **not** covered by any signature. Anyone who observes a broadcast/mempool `payout_tx` (or otherwise obtains the withdrawal outpoint + signature + user output, all of which travel over the network to be broadcast) can reconstruct a byte-identical transaction except for a different `OP_RETURN` payload (e.g., a different operator's `xonly_pk`, or garbage bytes), reuse the exact same witness, and get their version confirmed instead (e.g., via RBF with a higher fee).

Verifiers derive the "who fronted this payout" attribution purely from whichever version of the `OP_RETURN` output actually gets mined: [3](#0-2) 

This attribution then drives two critical decisions:

1. `is_kickoff_malicious` treats a missing/invalid `operator_xonly_pk` in the payout `OP_RETURN` as proof the kickoff is malicious: [4](#0-3) 

2. `get_first_unhandled_payout_by_operator_xonly_pk` lets whichever operator's key appears in the confirmed `OP_RETURN` claim the reimbursement for that withdrawal: [5](#0-4) 

The binding that must hold is:
```
payout_payer_operator_xonly_pk (as recorded by verifiers) == operator_xonly_pk of the party that actually fronted the withdrawal amount
```
Because the `OP_RETURN` is unsigned, an unprivileged network observer can break this equality without ever funding anything: the user still receives their money (output 0 is fixed by the signature), but the recorded "payer" can be forced to `None` or to an unrelated operator's key.

### Impact Explanation
Two concrete outcomes, both matching the specified Critical impacts:

- **`OP_RETURN` replaced with garbage / no valid pubkey** → `operator_xonly_pk` recorded as `None` → `is_kickoff_malicious` treats the legitimate operator's subsequent kickoff as malicious (`Ok(true)`), exposing the honest operator, who genuinely paid the user, to a challenge/disprove flow and loss of their `Reimburse`/round collateral — **an honest operator's collateral burned**, and simultaneously the deposit's move-to-vault UTXO can never be legitimately reimbursed — **a vault UTXO permanently frozen / an honest operator permanently unable to be reimbursed**.
- **`OP_RETURN` replaced with a different, real operator's `xonly_pk`** → `get_first_unhandled_payout_by_operator_xonly_pk` lets that unrelated operator claim reimbursement via the kickoff/reimburse flow for a withdrawal it never funded — **an operator reimbursed for a payout it never funded**.

### Likelihood Explanation
The precondition is only that the attacker can see the broadcast `payout_tx` (mempool visibility, which is public) or otherwise learn the withdrawal outpoint/user-signature/output pair, and can get a competing version of the transaction mined instead (e.g., via a higher-fee replacement broadcast). No verifier, operator, watchtower, aggregator, or key-compromise role is required — this only requires ordinary, unprivileged network participation (observing mempool traffic and broadcasting a competing transaction), which is exactly the kind of "unauthenticated broadcasting call" scenario the rules call out as in-scope. The construction itself (swap output 2, keep the witness) is trivial once the signed inputs are known.

### Recommendation
Bind the `operator_xonly_pk` (payer attribution) to the transaction using a mechanism the user's signature (or another authenticated party) actually commits to, e.g.:
- Have the aggregator/user sign over the full output set (including the `OP_RETURN`) instead of `SIGHASH_SINGLE|ANYONECANPAY`, or
- Require operators to additionally provide a separate signed commitment to `(withdrawal_id, operator_xonly_pk)` that verifiers check off-chain before trusting the `OP_RETURN`, rather than relying solely on whichever `OP_RETURN` happens to be mined.

### Proof of Concept
1. Operator O broadcasts `payout_tx_O` spending withdrawal UTXO `W` with witness `sig` (SIGHASH_SINGLE|ANYONECANPAY) and `OP_RETURN = O.xonly_pk` (`core/src/builder/transaction/operator_reimburse.rs:407-436`).
2. Attacker A observes `payout_tx_O` in the mempool, extracts `(W, sig, output0)`.
3. A constructs `payout_tx_A`: same input `W` + witness `sig`, same output 0, same/adjusted anchor, but `OP_RETURN` replaced with garbage bytes (or another real operator `B.xonly_pk`), with a higher fee, and broadcasts/RBFs it.
4. `payout_tx_A` confirms instead of `payout_tx_O`. The user still receives the exact payout (output 0 unchanged).
5. Verifiers run `update_finalized_payouts`, parse the confirmed `OP_RETURN`, and record `payout_payer_operator_xonly_pk` as `None` (or `B`) (`core/src/verifier.rs:2312-2328`).
6. On O's kickoff, `is_kickoff_malicious` returns `true` (payer mismatch/None) even though O genuinely paid, exposing O's collateral to disprove/challenge; or `B` fraudulently qualifies via `get_first_unhandled_payout_by_operator_xonly_pk` to claim reimbursement it never earned.

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

**File:** core/src/operator.rs (L614-637)
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
```

**File:** core/src/verifier.rs (L1875-1890)
```rust
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
