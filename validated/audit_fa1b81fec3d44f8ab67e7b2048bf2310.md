### Title
Payout OP_RETURN operator attribution is unauthenticated under SIGHASH_SINGLE|ANYONECANPAY, letting anyone reassign or erase reimbursement credit for a real withdrawal payout - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The bridge attributes "who fronted a withdrawal" (and is therefore owed reimbursement) solely from the OP_RETURN output of whichever `Payout` transaction confirms on-chain for a given withdrawal UTXO. That OP_RETURN output is not covered by the user's `SinglePlusAnyoneCanPay` signature, so anyone who has seen that signature (it travels over the public `Withdraw`/`InternalWithdraw` RPCs and appears in the broadcast/mempool transaction) can rebuild an alternate, still-validly-signed payout transaction that pays the user correctly but swaps or drops the operator attribution field, breaking the binding between "the operator credited for reimbursement" and "the party that actually funded the payout."

### Finding Description
`create_payout_txhandler` builds the `Payout` transaction with a single signed input (the withdrawal UTXO) and three outputs: the user payout (index 0), an anchor (index 1), and an OP_RETURN carrying `operator_xonly_pk` (index 2). [1](#0-0) 

The witness is produced with the user's `taproot::Signature`, and the sighash type is strictly enforced to be `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1) 

Under `SIGHASH_SINGLE | ANYONECANPAY`, the signature only commits to: (a) the single input being spent (no linkage to any other inputs the transaction may carry, since `ANYONECANPAY` excludes the other-inputs commitment), and (b) exactly one output at the same index as the signed input (index 0 here). The anchor output (index 1) and, critically, the OP_RETURN output (index 2) that names the crediting operator are **not** covered by the user's signature at all.

The verifier later determines "who paid" purely by reading the OP_RETURN of whatever payout transaction is actually confirmed on-chain for that withdrawal, with no cross-check against who actually supplied the value: [3](#0-2) 

This recorded `payout_payer_operator_xonly_pk` is what `PayoutCheckerTask` uses to decide which operator "owns" this payout and should proceed to kickoff/reimbursement: [4](#0-3) [5](#0-4) 

Because the OP_RETURN (and any additional inputs/outputs, thanks to `ANYONECANPAY`) is unauthenticated by the user's signature, an attacker who has captured the withdrawal `input_signature` — reachable via the public `ClementineAggregator::Withdraw` / `ClementineOperator::Withdraw`/`InternalWithdraw` RPCs, or simply observable in the mempool once an honest operator broadcasts its own `Payout` tx — can construct a competing, still-validly-signed transaction that pays the user identically but:
- names a different (victim) operator's xonly public key in OP_RETURN, or
- omits/corrupts the OP_RETURN so `operator_xonly_pk` parses to `None`.

If this alternate transaction confirms instead of (or as a replacement/race against) the honest operator's original broadcast, the equality the protocol relies on — `operator credited in OP_RETURN == operator whose funds are represented by the on-chain payout` — is broken.

### Impact Explanation
If the malleated tx drops or corrupts the OP_RETURN (parses to `None`), the honest operator who actually fronted the withdrawal will never appear as the `payout_payer_operator_xonly_pk` for that withdrawal, so `get_first_unhandled_payout_by_operator_xonly_pk` never returns it to that operator's `PayoutCheckerTask`, and `handle_finalized_payout`/kickoff/reimbursement is never triggered for the real payer — an honest operator permanently unable to be reimbursed for a payout it genuinely funded. If instead the OP_RETURN names a different operator, that operator's automation will treat an on-chain event it did not cause as its own, driving it into the kickoff/reimbursement flow for a payout it never funded, misattributing reimbursement credit away from the true payer. Both outcomes are direct breaks of the custody/attribution binding between the party that paid and the operator credited for reimbursement.

### Likelihood Explanation
No privileged role (verifier/operator/aggregator key) is required: the withdrawal `input_signature` is transmitted through client-facing RPCs and/or is visible once the honest operator's payout transaction is broadcast to the mempool, and Bitcoin's `SIGHASH_SINGLE|ANYONECANPAY` semantics make constructing an alternative, still-valid transaction with a different OP_RETURN/inputs a standard, well-understood signature-malleability technique — no protocol-level check ties the confirmed payout's OP_RETURN back to the entity that actually supplied the payout's funds.

### Recommendation
Do not use an unauthenticated OP_RETURN as the sole source of truth for reimbursement attribution. Either have the user's signature cover the operator-identity output as well (e.g., use `SIGHASH_ALL` or otherwise commit to the OP_RETURN output/index in the signed message), or bind the operator's identity/eligibility to something the operator itself signs and that is independently verifiable (e.g., require the crediting operator to co-sign or commit to the payout transaction, and validate that the actual value in the payout output structurally traces back to funds contributed by the credited operator) rather than trusting an unauthenticated output.

### Proof of Concept
1. Operator O broadcasts its `Payout` transaction: input = withdrawal UTXO (signed by user with `SinglePlusAnyoneCanPay`), output[0] = user payout, output[1] = anchor, output[2] = OP_RETURN(O.xonly_pk), per `create_payout_txhandler`.
2. Attacker A observes this transaction in the mempool (or observes `input_signature` from the `Withdraw` RPC call before O broadcasts).
3. A constructs Tx': same input (same signature, since `SIGHASH_SINGLE|ANYONECANPAY` doesn't cover the input set or output[2]), same output[0] (required by the signature commitment), but replaces output[1]/output[2] (e.g., no OP_RETURN, or OP_RETURN naming a different xonly_pk), possibly adding attacker's own funding input via `ANYONECANPAY`.
4. If Tx' confirms instead of O's original broadcast, `update_finalized_payouts` records the withdrawal's `payout_payer_operator_xonly_pk` as `None` or as the substituted key rather than O — O's `PayoutCheckerTask` (`get_first_unhandled_payout_by_operator_xonly_pk`) never sees this withdrawal as its own, and O can never trigger `handle_finalized_payout`/kickoff/reimbursement for a withdrawal it genuinely fronted.

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

**File:** core/src/verifier.rs (L2298-2343)
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
```

**File:** core/src/task/payout_checker.rs (L39-52)
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
