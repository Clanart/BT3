### Title
Unauthenticated OP_RETURN ordering in payout_tx lets an attacker hijack operator reimbursement attribution - (File: circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`get_first_op_return_output` blindly returns the first `OP_RETURN` output found in a payout transaction via `.find()`, with no check that it is unique or at a canonical index. Both `bridge_circuit`'s `deposit_constant` computation and `Verifier::update_finalized_payouts`'s DB attribution rely on this same unauthenticated selection, so whichever `OP_RETURN` appears earliest in the output list determines which operator's x-only pubkey is bound into `deposit_constant`/`journal_hash` and stored in `withdrawals.payout_payer_operator_xonly_pk`.

### Finding Description
The binding that must hold is: `operator_xonlypk` used in `deposit_constant`/`journal_hash` (circuits-lib) and `withdrawals.payout_payer_operator_xonly_pk` (DB) == the x-only pubkey of the operator who actually funded/authored `payout_tx.output[0]` (the user payout).

`get_first_op_return_output` implements this as "the first `OP_RETURN` output in transaction-output order, full stop": [1](#0-0) 

This exact function is called both in `bridge_circuit` to compute `operator_xonlypk` for `deposit_constant`/`journal_hash`: [2](#0-1) 

and in `Verifier::update_finalized_payouts` to compute the DB's `operator_xonly_pk` attribution for a payout: [3](#0-2) 

The canonical operator-authored `payout_tx` construction places output 0 = user payout, output 1 = anchor, output 2 = the operator's `OP_RETURN`: [4](#0-3) 

Neither the circuit code (lines 137-245 of `bridge_circuit`) nor `update_finalized_payouts` validates the total count of `OP_RETURN` outputs, their index, or that output 0's spender/funder matches the pubkey found in the (first) `OP_RETURN`. The only checks performed relate to `input[payout_input_index].previous_output` matching the withdrawal outpoint from the storage proof: [5](#0-4) 

Downstream, `Verifier::is_kickoff_malicious` trusts this DB attribution as ground truth and compares it against the kickoff-claiming operator's own key: [6](#0-5) 

If the DB attribution was hijacked to an attacker-chosen or unrelated pubkey, the truly-funding operator's kickoff will be judged malicious (permanent denial of reimbursement), or, if the attacker instead injects a real-but-uninvolved operator's pubkey as the first `OP_RETURN`, that uninvolved operator's kickoff will pass this check and it can claim `Reimburse` for a payout it never funded.

Root cause: `get_first_op_return_output` has no notion of "the operator's designated OP_RETURN slot"; it is a generic scan that is trivially confused by any additional `OP_RETURN` output preceding the legitimate one, and both consumers (circuit + DB) share this same unauthenticated logic so they'd agree with each other even when both are wrong.

I was not able to fully confirm within the available investigation budget whether the withdrawal input's signature is actually `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` (as asserted in the question) and, if so, whether the pre-signed input/signature enabling reconstruction of a competing `payout_tx` is exposed to an unprivileged, non-operator attacker (versus being distributed only to registered operators over an authenticated channel). This is a material precondition for the "attacker broadcasts a competing payout_tx" scenario and could not be verified from the indexed code within the iteration limit.

### Impact Explanation
If the precondition holds (an unprivileged party can replay/complete the withdrawal-authorizing signature and freely append extra outputs), the attacker can: (1) permanently deny reimbursement to the honest operator who funded the payout, since `is_kickoff_malicious` will flag their kickoff as malicious against the hijacked DB attribution, or (2) cause a real, uninvolved operator to be falsely credited and able to claim `Reimburse` for funds it never sent. Both fall under the Critical severity bucket ("an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed"). The blast radius is per-withdrawal and repeatable across any deposit/withdrawal where the attacker can win this race or double-spend, but scoped to disputes over reimbursement/OP_RETURN attribution rather than direct BTC theft from a vault.

### Likelihood Explanation
Likelihood is contingent and I could not fully validate it: it requires (a) the withdrawal input signature to genuinely be `SIGHASH_SINGLE|ANYONECANPAY`-style (only committing input 0/output 0), and (b) that signature/UTXO data to be obtainable and replayable by an unprivileged attacker before/instead of the legitimate operator's broadcast. If both hold, the attack is cheap (one extra `OP_RETURN` output, negligible fee cost) and repeatable per withdrawal. If either precondition is false (e.g., operators alone hold the presigned data over an authenticated gRPC channel, or the sighash type actually commits to all outputs), the described attack path does not exist and this reduces to a code-quality/defense-in-depth issue rather than an exploitable Critical bug.

### Recommendation
Do not rely on "first OP_RETURN found" as an authenticated signal. Options: (1) enforce a fixed, protocol-defined output index for the operator's `OP_RETURN` (e.g., always output index 2, matching `create_payout_txhandler`) and reject/ignore transactions where that index is not an `OP_RETURN`, in both `get_first_op_return_output`'s callers; (2) additionally require that there is exactly one `OP_RETURN` output in the transaction, panicking/rejecting otherwise; (3) cryptographically bind the operator identity into a value that is covered by a signature over the whole transaction (not just input 0/output 0), so an attacker cannot append extra outputs without invalidating some check.

### Proof of Concept
Because I could not confirm the exact sighash type and distribution model for the withdrawal-authorizing signature, I cannot certify a runnable `cargo test` today. A background engineer should:
1. Locate where the withdrawal input's signature is generated/validated (search `set_p2tr_key_spend_witness` callers and sighash-flag construction near `core/src/actor.rs`/`core/src/builder/transaction/deposit_signature_owner.rs`) to confirm the actual `sighash_type` used for `payout_tx` input 0, and confirm whether that signed material (input UTXO + signature) is exposed to non-operator, unprivileged callers.
2. If confirmed as `SIGHASH_SINGLE|ANYONECANPAY`, write a `circuits-lib` test that constructs a `BridgeCircuitInput` whose `payout_spv.transaction` has: output 0 = the committed user payout (satisfying the existing input/vout assertions), output 1 = an attacker `OP_RETURN` with an attacker-chosen x-only pubkey, output 2 = the real funding operator's `OP_RETURN`. Assert that `get_first_op_return_output` returns the attacker's `OP_RETURN`, that the resulting `deposit_constant`/`journal_hash` binds to the attacker's pubkey, and mirror the same transaction through `Verifier::update_finalized_payouts`'s logic to show the DB's `operator_xonly_pk` also resolves to the attacker's value instead of the real funding operator's.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L188-222)
```rust
    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );

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

**File:** core/src/verifier.rs (L1871-1890)
```rust
        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
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

**File:** core/src/verifier.rs (L2311-2321)
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
