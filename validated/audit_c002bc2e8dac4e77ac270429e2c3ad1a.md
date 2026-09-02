### Title
Payout OP_RETURN reimbursement claim is unauthenticated and malleable via SIGHASH_SINGLE|ANYONECANPAY, permanently orphaning the fronting operator's reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `payout_tx`'s single input is signed by the withdrawer with `TapSighashType::SinglePlusAnyoneCanPay`, which only commits to input‑0 and output‑0 (the user payment). The operator‑identifying `OP_RETURN` output (index 2) that Clementine relies on to attribute the withdrawal to the fronting operator is never covered by that signature, so any unprivileged party who observes the unconfirmed `payout_tx` can rebuild a conflicting transaction with the same signed input/output but an arbitrary or foreign `OP_RETURN`, and win the confirmation race with a higher fee.

### Finding Description
The claimed binding is: `withdrawals.payout_payer_operator_xonly_pk` (derived by `update_finalized_payouts` from the `OP_RETURN` of whichever transaction canonically spends `w.withdrawal_utxo_txid:vout`, per `get_payout_txs_for_withdrawal_utxos`) `== kickoff_data.operator_xonly_pk` (the operator that actually built and broadcast the real `payout_tx`), which `is_kickoff_malicious` checks at [1](#0-0) .

`create_payout_txhandler` builds the payout tx with the input spent via `SpendPath::KeySpend` and signs only that input with the user's signature, while the `OP_RETURN` (containing the fronting operator's xonly pubkey) is added as output index 2, appended after signing: [2](#0-1) . The signature's sighash type is enforced to be `SinglePlusAnyoneCanPay`: [3](#0-2) . `SIGHASH_SINGLE|ANYONECANPAY` commits only to input 0 and output 0; any other input or output — including the trailing anchor and `OP_RETURN` outputs — is unauthenticated and can be freely replaced by anyone holding the raw signature, since the signature itself is public once the tx is placed in the mempool (or even earlier, since `input_signature` is submitted as part of the public Citrea `withdraw()` call/gRPC request).

Exploit flow: after operator A broadcasts its real `payout_tx` (input i, output 0 = user payment, output 1 = anchor, output 2 = `OP_RETURN(A's xonly pk)`), an unprivileged attacker copies input 0 (with its witness/signature) and output 0 unchanged, and constructs an alternate transaction with the same input, the same required output 0, but a different anchor amount (to raise the fee) and a mangled or third‑party `OP_RETURN`. Because this alternate transaction spends the identical outpoint, it is a valid double‑spend candidate; if it is mined instead of A's original (trivially achievable by paying a higher fee, exploiting standard fee-based mempool eviction/full‑RBF policies that most miners run today), `bitcoin_syncer_spent_utxos` records the attacker's txid as the canonical spender of `w.withdrawal_utxo_txid:vout`: <cite repo="Annirich/clementine--025" path

### Citations

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

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```
