### Title
Payout attribution (operator OP_RETURN) is not covered by the withdrawal signature, allowing a mempool replacement to permanently misattribute reimbursement credit - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `Payout` transaction that fronts a user's Citrea withdrawal is authorized by a user-supplied Schnorr signature using `SIGHASH_SINGLE|ANYONECANPAY`. That sighash flavor only binds input 0 and the output at the *same index* (output 0, the user's payout). The second data output — an `OP_RETURN` carrying the fronting operator's x-only pubkey, which is the sole on-chain artifact later used to attribute reimbursement credit — is **not covered by any signature at all**. Because the `Payout` transaction is broadcast with `FeePayingType::RBF`, anyone observing the mempool can, before confirmation/finality, rebroadcast a fee-bumped replacement that reuses the same publicly-visible signature for input/output 0 but swaps the `OP_RETURN` payload to an arbitrary or unregistered x-only pubkey. This permanently corrupts the payer attribution the verifiers use to decide who gets reimbursed.

### Finding Description
`create_payout_txhandler` builds the `Payout` tx with a `KeySpend` witness set only from the user's signature over input 0, and an unsigned `OP_RETURN` output carrying `operator_xonly_pk`: [1](#0-0) 

The operator constructs and verifies this signature explicitly documenting the `SinglePlusAnyoneCanPay` requirement: [2](#0-1) 

`SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0); `ANYONECANPAY` additionally allows arbitrary other inputs to be added/removed. Neither covers output index 2 (the `OP_RETURN`) nor output index 1 (the anchor). Consequently the operator-attribution data is unauthenticated with respect to the actual payer.

The `Payout` transaction type is explicitly sent using Bitcoin RBF (replace-by-fee), which by protocol design allows *any* party to supersede an unconfirmed mempool transaction with a conflicting, higher-fee transaction as long as it produces valid witnesses for every input: [3](#0-2) 

Since the only witness needed for input 0 is the user's signature — which becomes public the moment the original `Payout` tx is broadcast to the network/mempool — an unprivileged observer can copy that signature into a new transaction with the same input and output 0, but an arbitrary `OP_RETURN` payload, and get it accepted as a valid RBF replacement.

Verifiers derive payer attribution purely from parsing this `OP_RETURN` of whichever transaction actually confirms, with no cross-check against who broadcast/funded the original transaction: [4](#0-3) 

This attribution is persisted and becomes the sole database record of "who fronted this withdrawal": [5](#0-4) 

Downstream, reimbursement eligibility is gated strictly on this stored attribution matching the operator's own key: [6](#0-5) 
and a kickoff is flagged malicious if the recorded payer xonly-pk doesn't match the kickoff's operator: [7](#0-6) 

**Binding broken:** `payout_payer_operator_xonly_pk` (recorded attribution) == the x-only public key of the operator whose funds actually paid the user's withdrawal UTXO. Before/after the attacker's mempool replacement, the actual payer (whoever's wallet inputs/fees funded the transaction) is unchanged, but the recorded attribution can be rewritten to any value, breaking this equality.

### Impact Explanation
If the `OP_RETURN` is rewritten to a value that never matches any real operator's key (any 32-byte value that parses as a valid x-only public key qualifies — it need not belong to a registered operator), `validate_payer_is_operator` and `is_kickoff_malicious` will never accept a kickoff for this withdrawal from the operator who actually fronted the funds. That operator has already paid the user off-chain-equivalent value (the BTC left their control to satisfy the Citrea withdrawal) but can never obtain the on-chain finding required to unlock reimbursement — this is "an honest operator permanently unable to be reimbursed," a Critical-severity impact per the custody model. Alternatively, an attacker could attribute the payout to a *different, real* operator, causing that operator's automation to treat the kickoff/payout data as inconsistent with its own state, or causing `send_asserts`'s equality check (`payout_op_xonly_pk != kickoff_data.operator_xonly_pk`) to permanently reject the true operator's reimbursement attempts: [8](#0-7) 

### Likelihood Explanation
The attack requires no privileged role, key, or certificate: it only requires observing an unconfirmed `Payout` transaction in the mempool (a public network) during the window before it reaches `finality_depth` confirmations, and rebroadcasting a fee-bumped conflicting transaction with the same input/output-0 data but a different `OP_RETURN`. This is a pure Bitcoin-protocol-level RBF action reachable by anyone monitoring mempools, with no reliance on TLS interception, node compromise, or majority hashrate.

### Recommendation
Bind the operator attribution cryptographically to the actual authorization for the payout, e.g. by having the fronting operator sign (with their own key) a commitment that includes the `OP_RETURN` payload, and have verifiers validate that signature rather than trusting the raw `OP_RETURN` bytes of whichever transaction confirms. Alternatively, change the sighash strategy so the operator-identifying output is covered (e.g., use `SIGHASH_ALL` for the operator's own additional input/signature that funds the transaction, and derive attribution from the address that supplied the paying UTXO(s) instead of an unauthenticated `OP_RETURN`).

### Proof of Concept
1. An operator, `Op_A`, receives a legitimate user withdrawal signature (`SIGHASH_SINGLE|ANYONECANPAY`) and broadcasts `Payout` tx `T1` via `withdraw()`, with witness = `[user_sig]` on input 0, output 0 = user payment, output 2 = `OP_RETURN(Op_A.xonly_pk)` (`core/src/builder/transaction/operator_reimburse.rs:407-436`, `core/src/operator.rs:620-637`).
2. `T1` enters the mempool; per `tx_sender_queue.rs:92-105` it is RBF-enabled.
3. An unprivileged observer extracts the public witness signature from `T1` (visible in the mempool/any node) and constructs `T2`: identical input 0 (same witness signature, reused, valid since only input 0 + output 0 are signature-committed under `SIGHASH_SINGLE|ANYONECANPAY`), identical output 0, but `OP_RETURN` output replaced with an arbitrary/unregistered x-only pubkey, and a higher fee to satisfy RBF rules.
4. Observer broadcasts `T2`; it replaces `T1` in mempools and eventually confirms.
5. During block sync, `update_finalized_payouts` parses `T2`'s `OP_RETURN` and records the corrupted attribution via `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/verifier.rs:2311-2350`, `core/src/database/verifier.rs:198-251`).
6. `Op_A` (who genuinely funded the withdrawal for the user) later calls the reimbursement flow; `validate_payer_is_operator` fails because the DB's `payout_payer_operator_xonly_pk` no longer equals `Op_A`'s key (`core/src/operator.rs:1703-1729`), and/or `is_kickoff_malicious` flags any kickoff `Op_A` attempts as malicious (`core/src/verifier.rs:1882-1890`). `Op_A` can never be reimbursed for this payout.

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

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
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

**File:** core/src/tx_sender_queue.rs (L92-105)
```rust
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
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

**File:** core/src/verifier.rs (L2311-2343)
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
