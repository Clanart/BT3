### Title
Payout attribution (`OP_RETURN` operator xonly-pubkey) is unauthenticated and malleable under `SIGHASH_SINGLE|ANYONECANPAY`, allowing reimbursement credit to be misattributed — (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The payout transaction that "fronts" a withdrawal commits the fronting operator's identity via a plaintext `OP_RETURN` output containing the operator's x-only public key. This output is not covered by the user's `SinglePlusAnyoneCanPay` signature, so it is freely rewritable by anyone who can construct/relay a competing version of the transaction before confirmation. The verifier and operator later trust this on-chain `OP_RETURN` value as the authoritative record of "who fronted this payout" for reimbursement purposes, exactly mirroring the reported bug class: a self-declared identity claim used for attribution/authorization without a real cryptographic binding.

### Finding Description
`create_payout_txhandler` builds the payout tx with one signed input (the withdrawal UTXO, spent via `SpendPath::KeySpend` with the user's signature) and three outputs: the user payout output (index 0), an anchor (index 1), and an `OP_RETURN` output encoding `operator_xonly_pk` (index 2), which is the value later used to attribute the payout to a specific operator: [1](#0-0) 

The RPC layer accepts `output_script_pubkey`/`output_amount`/`input_signature`/`input_outpoint` as caller-supplied parameters, and the withdrawal input/signature is documented as the *user's* input and signature: [2](#0-1) 

Because the signature type is `SinglePlusAnyoneCanPay`, it cryptographically commits only to input 0 and the output at the matching index (0). The anchor output and the `OP_RETURN` operator-identity output are outside the signed message, so any party who has (or observes) this transaction before it confirms can substitute a different `operator_xonly_pk` in the `OP_RETURN` — or strip it entirely — without invalidating the user's signature, and race to get their version mined.

The verifier trusts this unauthenticated `OP_RETURN` field as ground truth for "who fronted the payout": it is parsed directly from the confirmed transaction and stored as `payout_payer_operator_xonly_pk`: [3](#0-2) 

This stored value is then used by the operator to authorize its own reimbursement path — if the stored `payer_xonly_pk` doesn't match `self.signer.xonly_public_key`, the operator's reimbursement flow is rejected: [4](#0-3) 

The verifier's kickoff-honesty check (`is_kickoff_malicious`) likewise treats the `OP_RETURN`-derived `operator_xonly_pk` as the authoritative binding between kickoff and payout: [5](#0-4) 

This is the same bug pattern as the reported `PoolFactory.deployPool()` issue: an unauthenticated, self-declared value (there, `oracleWrapper.deployer()`; here, the `OP_RETURN` operator pubkey) is used to establish an ownership/authorization fact, and the report itself notes this class of check "protects against frontrunning" only when nobody else can race to set the same field first — which is exactly the malleability window created here by `SinglePlusAnyoneCanPay`.

### Impact Explanation
If an attacker (or a competing party) captures the in-flight payout transaction before confirmation and rebroadcasts a variant with a different `OP_RETURN` operator pubkey (or none at all), the on-chain record of "who fronted this withdrawal" no longer matches the operator that actually serviced it. Consequences per the DB/consumer logic above:
- The honest operator that intended to front the payout can be permanently locked out of reimbursement for that withdrawal, since `validate_payer_is_operator` rejects any operator whose key doesn't match the (now attacker-controlled) `payout_payer_operator_xonly_pk`.
- Reimbursement credit can be misattributed to an arbitrary operator xonly-pubkey that never authorized or intended to service this withdrawal.

This aligns with the Critical-severity bullets "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation requires only visibility into an unconfirmed payout transaction (a public mempool artifact) and the ability to relay a modified, equally-valid transaction — no privileged role, key compromise, or insider access is needed, because the vulnerable outputs are outside the scope of the only signature present. The main open question (which I could not fully verify from the available index) is the precise provenance of the `input_utxo`/`in_outpoint` — i.e., whether it is a user-owned pre-committed UTXO (as the docstrings state) in all configurations, which determines how easily a withdrawing user themselves could obtain and freely rebroadcast this transaction without any operator involvement at all.

### Recommendation
Bind the fronting operator's identity to the transaction using a signature that actually covers the `OP_RETURN` output (e.g., require the operator to co-sign the payout tx, or use a sighash type that commits to all outputs), rather than relying on an unauthenticated plaintext field that is added outside the scope of the user's `SinglePlusAnyoneCanPay` signature.

### Proof of Concept
1. Observe an unconfirmed payout transaction created via `create_payout_txhandler`, whose only signature is `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` over input 0 / output 0 [6](#0-5) .
2. Reconstruct an alternate transaction reusing the same signed input/output pair, but replace the `OP_RETURN` output (index 2) with an arbitrary operator's x-only public key (or garbage), and rebroadcast with a competing fee via the anchor CPFP path.
3. Once confirmed, `update_finalized_payouts` records the attacker-chosen key as `payout_payer_operator_xonly_pk` [7](#0-6) .
4. The operator that actually intended to service the withdrawal fails `validate_payer_is_operator` and cannot obtain reimbursement transactions for this deposit [8](#0-7) .

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

**File:** core/src/operator.rs (L546-554)
```rust
    /// # Parameters
    ///
    /// - `withdrawal_idx`: Citrea withdrawal UTXO index
    /// - `in_signature`: User's signature that is going to be used for signing
    ///   withdrawal transaction input
    /// - `in_outpoint`: User's input for the payout transaction
    /// - `out_script_pubkey`: User's script pubkey which will be used
    ///   in the payout transaction's output
    /// - `out_amount`: Payout transaction output's value
```

**File:** core/src/operator.rs (L1703-1729)
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
