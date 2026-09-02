Based on my investigation, this confirms `SIGHASH_SINGLE|ANYONECANPAY` is explicitly enforced as the required sighash type for the user's withdrawal signature, and this is used specifically to determine the reimbursement operator credit via unauthenticated OP_RETURN data.

### Title
Unauthenticated OP_RETURN operator attribution in payout transaction allows misattributed reimbursement credit due to `SinglePlusAnyoneCanPay` malleability - (File: `core/src/verifier.rs`, `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The withdrawal ("payout") transaction's user-signed input uses `SinglePlusAnyoneCanPay` sighash, which by design only covers input 0 and output 0 (the user's payout output) [1](#0-0) . The OP_RETURN output that records which operator's `xonly_pk` fronted the withdrawal, and thus which operator is credited for reimbursement, is added at output index 2 and is not covered by that signature [2](#0-1) . The verifier later parses this unauthenticated OP_RETURN field directly from the confirmed on-chain transaction to decide which operator gets reimbursement credit [3](#0-2) .

### Finding Description
`create_payout_txhandler` builds the payout transaction with three outputs: the user's payout output (index 0, signed), an anchor output (index 1, unsigned), and an OP_RETURN output containing the operator's x-only public key (index 2, unsigned) [2](#0-1) . Only the input and output at index 0 are covered by the user's `SinglePlusAnyoneCanPay` signature; that sighash flag is explicitly enforced server-side, meaning the protocol accepts and expects transactions where any party can attach arbitrary additional inputs (to pay fees) and arbitrary additional outputs, including the OP_RETURN [4](#0-3) .

Once such a transaction confirms, `update_finalized_payouts` extracts the `operator_xonly_pk` purely by parsing the OP_RETURN bytes of whatever transaction happened to spend the withdrawal outpoint — with no cryptographic binding tying that OP_RETURN value to the party that actually supplied the fee-paying inputs [5](#0-4) . This value is persisted as `payout_payer_operator_xonly_pk` and is the sole signal `PayoutCheckerTask` uses to decide which operator's automation should claim reimbursement via `handle_finalized_payout` [6](#0-5) .

This breaks the intended binding: `operator credited for reimbursement == party that actually fronted (paid the fees for) the withdrawal`. Because the malleable outputs are unsigned, any unprivileged party who observes the user's signed, broadcast-or-broadcastable input (public once in the mempool) can rebuild a competing transaction using the same signed input/output-0 pair, add their own fee-paying inputs, and write an arbitrary 32-byte value into the OP_RETURN — including a genuine, uninvolved operator's `xonly_pk`, or an unparseable value to force the "optimistic payout" (no-operator-credited) fallback path.

### Impact Explanation
If an attacker gets their malleated transaction confirmed instead of the legitimate one:
- An honest operator who never fronted any funds could be automatically credited via `PayoutCheckerTask`/`handle_finalized_payout`, triggering that operator's own automation to build a kickoff and claim reimbursement it never actually funded — "an operator reimbursed for a payout it never funded" (Critical).
- Conversely, the operator that actually paid the user out-of-band and intended to broadcast its own attributed payout transaction could be permanently denied credit if its OP_RETURN is overwritten (an unparseable/mismatched value forces the DB to record `operator_xonly_pk = None`), leaving no operator eligible to claim reimbursement for that withdrawal — "an honest operator permanently unable to be reimbursed" (Critical).

### Likelihood Explanation
Exploiting this requires no privileged key, operator role, or verifier/aggregator access — only observing a broadcast (or about-to-be-broadcast) payout transaction's signed input/output pair, which is public, and winning a fee-rate race to get a substitute transaction confirmed instead. This is a plausible but non-trivial attack: it requires the attacker to fund and win a transaction-replacement race against the legitimate broadcaster, which introduces timing/fee competition dependent on network conditions. I was not able to fully verify from the available files whether any additional server-side reconciliation exists (e.g., cross-checking the confirmed OP_RETURN against a `tx_sender`-recorded intended broadcaster/operator by txid) that might mitigate this before `handle_finalized_payout` acts — this residual uncertainty should be resolved with a full code review, since it materially affects severity.

### Recommendation
Do not rely on an unsigned/unauthenticated OP_RETURN field to attribute reimbursement credit. Either (a) require the operator's own signature to cover the OP_RETURN output (e.g., via a covenant/2nd signature that binds the identity to the transaction), or (b) have the operator's own `tx_sender` record the txid and intended `operator_xonly_pk` at broadcast time and require `update_finalized_payouts`/`PayoutCheckerTask` to cross-check the on-chain OP_RETURN against that internally-recorded expectation before crediting any operator, rejecting mismatches rather than trusting the on-chain OP_RETURN alone.

### Proof of Concept
1. A user requests a withdrawal and produces a `taproot::Signature` over the payout transaction's input 0 / output 0 with `SinglePlusAnyoneCanPay`, as required by `parse_withdrawal_sig_params` [7](#0-6) .
2. Operator O1 legitimately builds and is about to broadcast a payout tx with OP_RETURN = O1's `xonly_pk` via `create_payout_txhandler` [2](#0-1) .
3. Before O1's transaction confirms, an attacker (no special role) extracts the signed input 0 / output 0 pair (visible once submitted to network / mempool), and constructs a new transaction reusing that pair, adding their own fee-paying input(s), and setting the OP_RETURN to a different, genuine operator O2's `xonly_pk` (or to garbage bytes).
4. If the attacker's transaction confirms first, `update_finalized_payouts` records `payout_payer_operator_xonly_pk = O2` (or `None`) in the database [3](#0-2) .
5. O2's own `PayoutCheckerTask`, seeing itself credited, automatically invokes `handle_finalized_payout` and claims reimbursement it never funded — or, if the OP_RETURN was garbled, no operator can ever claim reimbursement for that withdrawal at all [6](#0-5) .

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

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-435)
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
```

**File:** core/src/verifier.rs (L2312-2342)
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
```

**File:** core/src/task/payout_checker.rs (L41-79)
```rust
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

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```
