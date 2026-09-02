## Analog Found: Unauthenticated OP_RETURN attribution in payout transaction allows permanent freeze of the move-to-vault UTXO

### Title
Payout transaction's operator-attribution `OP_RETURN` is not covered by the user's withdrawal signature, letting anyone sever the fronting-operator binding and permanently freeze reimbursement - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The Y2K bug class is "value allocated to a state (an epoch) becomes permanently unclaimable because the identifier used to determine payout eligibility gets nulled in an edge case." The Clementine analog: the field that binds a confirmed payout to the operator entitled to reimbursement is an `OP_RETURN` output in the `payout_tx` that is **not** committed by the only signature required to spend the withdrawal UTXO. Because the withdrawal input is signed with `SinglePlusAnyoneCanPay`, any party who observes that signature (e.g. via the public Bitcoin mempool once the fronting operator broadcasts) can construct and get confirmed an alternative payout transaction that satisfies the committed output (paying the user) while supplying a garbled or missing `OP_RETURN`. This nulls the operator attribution exactly like the nullified-epoch `finalTVL=0` in the report, and the downstream logic treats a null attribution as "assume malicious," permanently blocking reimbursement for that deposit.

### Finding Description
1. The withdrawal input signature is enforced to use `TapSighashType::SinglePlusAnyoneCanPay`: [1](#0-0) 

2. `create_payout_txhandler` builds the payout tx with output[0] = user payout (the only output committed by `SIGHASH_SINGLE`), and output[2] = an `OP_RETURN` carrying the fronting operator's x-only pubkey — but this output is outside the signature's commitment scope: [2](#0-1) 

3. When a block is synced, the verifier extracts the operator pubkey from the payout tx's `OP_RETURN`. If the `OP_RETURN` is missing or does not parse to a valid x-only pubkey, `operator_xonly_pk` is set to `None` in the database — explicitly documented as reachable "if... the operator constructed the payout tx wrong": [3](#0-2) 

4. That `None` is then treated as proof of malicious behavior for **any** future kickoff on that deposit, regardless of which operator sends it: [4](#0-3) 

5. The BitVM/assert reimbursement path independently requires a non-`None` attributed operator pubkey and errors out otherwise, closing the second reimbursement route as well: [5](#0-4) 

Because `SinglePlusAnyoneCanPay` only commits to the spent input and the output at the same index, the operator's own funding inputs (added via `fund_raw_transaction`, typically signed `SIGHASH_ALL`) cannot be reused with a mutated `OP_RETURN` — but nothing stops an unrelated third party from building an entirely new transaction that reuses the valid, already-broadcast withdrawal signature, keeps the committed output paying the user, funds the transaction with the third party's own inputs, and races it into a block with a malformed/absent `OP_RETURN`. The binding this breaks: *the operator credited by the protocol* should equal *the operator that actually fronted the withdrawal*, but the attribution channel used to encode that equality is unauthenticated by the only signature gating the spend.

### Impact Explanation
Once a payout with a null/garbled attribution confirms, the deposit's escrowed BTC in the move-to-vault UTXO can never be released to any operator: `is_kickoff_malicious` treats `None` as malicious for every kickoff attempt tied to that deposit, and `send_asserts` independently refuses to proceed without a resolved operator pubkey. This matches the Critical impact category "a vault UTXO permanently frozen" — the honest operator that actually paid the withdrawing user is permanently unable to be reimbursed, and the deposited BTC is stuck exactly as the emissions were stuck in the referenced report when `finalTVL` was zeroed for a nullified epoch.

### Likelihood Explanation
Triggering this does not require any privileged role (operator/verifier/aggregator/etc.) — it only requires observing a broadcast-but-unconfirmed payout transaction on the public Bitcoin network and being able to relay a competing, self-funded transaction with the same committed input/output but a corrupted `OP_RETURN`. The economic cost to the attacker is paying the withdrawal amount to the user, which limits opportunistic exploitation to targeted griefing rather than routine occurrence; this is the main uncertainty in likelihood, since I could not fully verify from the indexed code whether the signature is exposed to unprivileged parties earlier than mempool broadcast (e.g., via a public Citrea contract event during withdrawal registration), which would make the attack cheaper/more likely to trigger opportunistically. That specific exposure path was not confirmed within available index coverage.

### Recommendation
Bind the operator-attribution `OP_RETURN` to the same signature that authorizes spending the withdrawal UTXO (e.g., require the user to co-sign the intended fronting operator, or move to a sighash type that commits to all outputs), and/or add an explicit recovery path (analogous to the report's proposed fix) so that a payout confirmed with an unresolved operator attribution does not permanently block reimbursement for the deposit — e.g., allow a re-attribution/appeal mechanism gated by proof of which party actually funded the payout inputs, or fail closed only for the specific malicious kickoff rather than for the deposit as a whole.

### Proof of Concept
Conceptual sequence:
1. Operator A broadcasts `payout_tx` for withdrawal `W`: input = dust UTXO signed by user with `SinglePlusAnyoneCanPay`, output[0] = user payout, output[2] = `OP_RETURN(A's xonly_pk)`, funded by A's own additional inputs (per `core/src/operator.rs` `withdraw`).
2. Before confirmation, an unprivileged observer of the mempool extracts `(input_outpoint, output_script_pubkey, output_amount, user_sig)` from A's broadcast transaction.
3. The observer constructs `payout_tx'`: same input+signature, same output[0] (still valid under `SIGHASH_SINGLE`), their own funding inputs/change, and a corrupted or absent `OP_RETURN`.
4. The observer gets `payout_tx'` confirmed instead of A's transaction (e.g., via higher fee).
5. `update_finalized_payouts` parses no valid pubkey from `payout_tx'`'s `OP_RETURN` and stores `operator_xonly_pk = NULL` for withdrawal `W`'s deposit (`core/src/verifier.rs` `update_finalized_payouts`).
6. Any subsequent kickoff by any legitimate operator for that deposit is now judged malicious by `is_kickoff_malicious` (`core/src/verifier.rs`), and `send_asserts` refuses to proceed (`core/src/operator.rs`), permanently blocking reimbursement and freezing the move-to-vault UTXO's funds.

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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
