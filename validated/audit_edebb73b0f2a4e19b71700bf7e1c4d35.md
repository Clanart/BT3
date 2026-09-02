## Title
Anyone can hijack operator payout attribution by exploiting `SIGHASH_SINGLE|ANYONECANPAY` and first-OP_RETURN parsing - (File: `circuits-lib/src/bridge_circuit/mod.rs`, `core/src/verifier.rs`)

### Summary
The withdrawal-payout signature uses `SinglePlusAnyoneCanPay`, which only commits to the single input being spent and the output at the same index — it does not commit to the number or contents of other inputs/outputs in the transaction. Both the operator's real payout tx (`create_payout_txhandler`) and any third party's constructed transaction that reuses this signed input can add arbitrary extra outputs, including an `OP_RETURN` output claiming an arbitrary "operator" xonly-pubkey. Both `update_finalized_payouts` (`core/src/verifier.rs`) and the bridge circuit (`get_first_op_return_output`, `circuits-lib/src/bridge_circuit/mod.rs`) attribute the payout/reimbursement to whichever xonly-pubkey happens to be embedded in the *first* `OP_RETURN` output found in the transaction, without verifying that this output-holder actually funded the withdrawal output.

### Finding Description
The user's payout authorization signature is required to be `SinglePlusAnyoneCanPay` (`core/src/rpc/parser/operator.rs:182`, `core/src/operator.rs:630-637`). Under BIP-341, `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` commits only to:
- the signing input's `previous_output` (via the amount/script computed for that one input, not `sha_prevouts`),
- the output at the same index as the input.

It does **not** commit to any other inputs or outputs in the transaction. This means a transaction that includes the legitimately-signed withdrawal input plus this required output at index 0 remains valid under the signature even if arbitrary additional inputs/outputs are appended — including an `OP_RETURN` output.

Two dependent places bind this transaction structure to "which operator gets credited/reimbursed for fronting the payout":

1. `core/src/verifier.rs::update_finalized_payouts` (lines ~2312-2343): for a payout tx observed on-chain, it calls `get_first_op_return_output(&circuit_payout_tx)` and parses whatever xonly-pubkey is in the first found `OP_RETURN` output as `operator_xonly_pk`, then stores this as `payout_payer_operator_xonly_pk` in the `withdrawals` table via `update_payout_txs_and_payer_operator_xonly_pk`. [1](#0-0) 

2. `circuits-lib/src/bridge_circuit/mod.rs::bridge_circuit` (lines 206-229) similarly calls `get_first_op_return_output(&input.payout_spv.transaction)` to extract the operator xonly-pubkey used to compute `deposit_constant`, which is committed into the final journal hash and used by the BitVM disprove/assert logic to bind the reimbursement claim to a specific operator. [2](#0-1) 

`get_first_op_return_output` simply returns the first output matching `is_op_return()`, with no validation that it is the *only* OP_RETURN output, nor that the party named in it is the one who actually funded/broadcast the correct payout output to the user: [3](#0-2) 

Because the signature scheme (`SinglePlusAnyoneCanPay`) does not bind the rest of the transaction, an unprivileged third party who observes a broadcast (or mempool) payout transaction containing the user's signed input can construct their own transaction reusing that same signed input, insert their own `OP_RETURN` with their own xonly-pubkey as the first output (before or in place of the real operator's OP_RETURN), and get that transaction confirmed first (e.g. by paying a higher fee / RBF), since nothing else in the design forces the real operator's payout transaction to be the one that lands on-chain — the withdrawal UTXO can only be spent once.

This is the same bug class as the reported `SwapAction::getSwapToken` issue: a function that deterministically picks "the" relevant element from an array/output-list (first vs. last, in that case; first found regardless of authorship in this case) without validating that the mode/authority of the surrounding data actually matches the assumption, causing a real custody/attribution binding — "the operator credited" — to diverge from "the party that actually paid" the withdrawal.

### Impact Explanation
If an attacker successfully gets their own transaction (reusing the user's `SinglePlusAnyoneCanPay` signature but with a different `OP_RETURN`) confirmed instead of, or interpreted in place of, the real operator's payout, the `payout_payer_operator_xonly_pk` recorded in the database — and the `operator_xonlypk` baked into the bridge-circuit `deposit_constant`/journal hash — would misattribute the withdrawal fronting to an operator (or arbitrary key) who did not actually pay the user. This directly breaks the custody-binding: "the operator credited versus the party that paid." Downstream, `PayoutCheckerTask`/`handle_finalized_payout` (`core/src/task/payout_checker.rs`) uses this exact attribution to authorize the operator's kickoff/reimbursement flow, and `is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) checks that a kickoff's operator matches the recorded `operator_xonly_pk`. An honest operator whose fronted payout gets attributed elsewhere due to this ambiguity could become unable to be reimbursed (Critical), or an attacker's own key could get falsely credited as having fronted a payout it never funded, enabling fraudulent reimbursement claims through the kickoff flow.

### Likelihood Explanation
Exploitation requires only that an unprivileged party observe a broadcast/mempool payout transaction using the user's `SinglePlusAnyoneCanPay` signature (public information once broadcast) and race a modified transaction reusing the same signed input with a different `OP_RETURN`, i.e. standard transaction pinning/replacement techniques on Bitcoin. No verifier, operator, or aggregator collusion is required. However, this depends on real-world details not fully confirmed from the available index (e.g., whether the operator's payout output is standard enough / whether replacement transactions with the same input but modified outputs would be relayable given RBF/mempool policy, and whether there are other implicit invariants elsewhere in the codebase — e.g. `create_payout_txhandler`'s fixed output ordering — that further constrain a substitute transaction). Given these open uncertainties about full exploitability under current mempool/RBF policy, likelihood is assessed as plausible but not fully proven from the indexed code alone.

### Recommendation
- Do not rely on "first OP_RETURN found" to determine payout attribution. Require and enforce a canonical output layout for the payout transaction (fixed indices, fixed number of outputs) and reject payout transactions that do not exactly match the expected shape produced by `create_payout_txhandler`/`create_optimistic_payout_txhandler`.
- Bind the operator attribution cryptographically to the signature, e.g., require the operator to co-sign the transaction (not just the user via `ANYONECANPAY`), or use a sighash mode that commits to the full set of outputs, so a third party cannot append/alter outputs while reusing a valid user signature.
- In `update_finalized_payouts` and in the bridge-circuit, validate that the observed payout transaction has exactly the outputs expected (payout output, anchor, single OP_RETURN) rather than scanning for the first OP_RETURN in an unconstrained output list.

### Proof of Concept
Conceptual sequence (not confirmed executable against current mempool policy without a live environment):
1. User signs withdrawal input with `SinglePlusAnyoneCanPay`, intending output[0] to be their payout and expecting operator A's `create_payout_txhandler` to add `output[2]` = OP_RETURN(A_xonly_pk).
2. Attacker observes the signed input (once broadcast or from the aggregator/operator gRPC flow) and constructs a new transaction: input = same signed UTXO, output[0] = same required user payout (to satisfy the SINGLE commitment), output[1..] = attacker's own `OP_RETURN(attacker_xonly_pk)` instead of operator A's.
3. Attacker gets this transaction confirmed (e.g., via fee bump / replacement) instead of operator A's.
4. `update_finalized_payouts` parses `get_first_op_return_output` from the confirmed tx and records `payout_payer_operator_xonly_pk = attacker_xonly_pk`, even though operator A is the one whose funds actually reached the user (or, alternatively, if neither party's funds actually reached the user via a materially different tx, no operator is properly credited at all), diverging the "operator credited" from "party that paid." [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
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

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

**File:** core/src/rpc/parser/operator.rs (L161-203)
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

    let input_outpoint: OutPoint = params
        .input_outpoint
        .ok_or_else(error::input_ended_prematurely)?
        .try_into()?;

    let users_intent_script_pubkey = ScriptBuf::from_bytes(params.output_script_pubkey);

    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
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
