### Title
Payout attribution relies on unauthenticated OP_RETURN data, allowing operator‑credit forgery for a payout it never funded - ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` determines which operator "fronted" a withdrawal solely by parsing the OP_RETURN output of the on-chain payout transaction, without any cryptographic binding between that OP_RETURN pubkey and the entity that actually funded/broadcast the transaction. Because the payout transaction's only signed input uses a `SIGHASH_SINGLE | ANYONECANPAY` signature from the user, the OP_RETURN output (at a different output index than the one covered by the signature) is not committed to by that signature and can be freely set or altered by whoever relays/finalizes the transaction. This is structurally the same defect class as the reported bug: a state/attribution update (`payout_payer_operator_xonly_pk`) is derived from an untrusted field instead of the actual party that moved the value, exactly matching the hinted binding "the operator credited versus the party that paid."

### Finding Description
The verifier scans the finalized block for the payout transaction and extracts the crediting operator purely from the OP_RETURN payload: [1](#0-0) 

This value is persisted as `payout_payer_operator_xonly_pk`: [2](#0-1) 

and is later used to look up "this operator's" unhandled payouts for the reimbursement/kickoff flow: [3](#0-2) 

The payout transaction itself is built with a single signed input (the user's withdrawal UTXO) and three outputs — payout, anchor, and the OP_RETURN carrying the fronting operator's xonly pubkey: [4](#0-3) 

The protocol documentation for the withdrawal parameters states the user's signature is `SinglePlusAnyoneCanPay`: [5](#0-4) 

`SIGHASH_SINGLE` only commits the signer to the output at the same index as the signed input (index 0, the user payout output); `ANYONECANPAY` permits anyone to add/replace additional inputs and outputs. The OP_RETURN output (index 2) is therefore outside the scope of what the user's signature authenticates, meaning the identity embedded in it is attacker-controllable by whoever relays or finalizes the transaction, while `update_finalized_payouts` treats it as ground truth for "who paid."

### Impact Explanation
If the OP_RETURN xonly pubkey can be set to an operator that did not actually broadcast/fund the payout, that operator's automated pipeline (`get_first_unhandled_payout_by_operator_xonly_pk`) will pick up the withdrawal as "its own" unhandled payout and proceed to build a kickoff/reimbursement chain for a payment it never made — i.e., an operator reimbursed for a payout it never funded, which falls under the Critical impact category defined in the rules.

### Likelihood Explanation
The likelihood is Medium-to-High for an unprivileged actor who can observe or relay payout transactions in the mempool/network: since the OP_RETURN is unauthenticated, any party relaying the transaction before it confirms can rewrite it to name any operator's xonly pubkey without needing any operator key, verifier key, or aggregator signature.

### Recommendation
Bind the OP_RETURN (or an equivalent commitment) cryptographically to the payout's actual funding, e.g., by having the operator sign the full transaction (covering all outputs including the OP_RETURN) rather than relying on data outside the scope of `SIGHASH_SINGLE|ANYONECANPAY`, or by validating during finalization that the named operator's own key/UTXO set is what actually funded the extra input(s)/outputs of the payout transaction before crediting `payout_payer_operator_xonly_pk`.

### Proof of Concept
1. User creates and hands the operator a `SIGHASH_SINGLE|ANYONECANPAY` signature authorizing spend of their withdrawal UTXO to the designated payout output (output index 0) — per `core/src/rpc/clementine.proto:243-246` and `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`).
2. An attacker (any unprivileged party observing the unconfirmed transaction, since ANYONECANPAY allows re-signing/relaying with modified non-committed outputs) rewrites the OP_RETURN output to embed a different operator's xonly public key instead of the actual funding operator's key, then relays/broadcasts the modified transaction.
3. Once finalized, `update_finalized_payouts` (`core/src/verifier.rs:2311-2350`) parses this attacker-chosen OP_RETURN and records the wrong operator as `payout_payer_operator_xonly_pk`.
4. The falsely credited operator's automation, via `get_first_unhandled_payout_by_operator_xonly_pk` (`core/src/database/verifier.rs:282-313`), treats this as its own unhandled payout and proceeds toward kickoff/reimbursement for a payout it never funded.

### Citations

**File:** core/src/verifier.rs (L2311-2328)
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
```

**File:** core/src/verifier.rs (L2337-2350)
```rust
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

**File:** core/src/rpc/clementine.proto (L243-246)
```text
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
```
