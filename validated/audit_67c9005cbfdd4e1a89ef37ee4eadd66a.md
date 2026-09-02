### Title
Unauthenticated payout-tx output substitution via `SIGHASH_SINGLE|ANYONECANPAY` malleability lets a decoy OP_RETURN hijack `payout_payer_operator_xonly_pk` - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs, circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`create_payout_txhandler` signs the payout transaction's input with a user signature that is required to use `SinglePlusAnyoneCanPay`, which only commits to input0 and output0, leaving every other output unauthenticated and freely rewritable by anyone who observes the broadcast/mempooled transaction. `get_first_op_return_output`/`parse_op_return_data`, consumed by `update_finalized_payouts`, blindly take the *first* OP_RETURN output in whatever transaction ultimately spends the recorded `withdrawal_utxo`, so an attacker who rebroadcasts a competing version of the same input+output0 with a decoy OP_RETURN placed first can rewrite which operator is credited with a payout it did not fund, or null out the honest operator's credit entirely.

### Finding Description
The binding this system relies on is:

`withdrawals.payout_payer_operator_xonly_pk == the xonly pk of the operator that actually funded withdrawals.withdrawal_utxo (input0/output0) via `Operator::withdraw`.`

The payout transaction is built by `create_payout_txhandler` [1](#0-0)  with input0 spent via `SpendPath::KeySpend` and signed with the caller-supplied `in_signature.sighash_type`. `Operator::withdraw` explicitly requires and only verifies that this is a `SinglePlusAnyoneCanPay` signature: [2](#0-1) . Under `SIGHASH_SINGLE|ANYONECANPAY`, the signature commits only to input0 and the output at the same index (output0); the OP_RETURN and anchor outputs are not covered.

`update_finalized_payouts` later determines the "payout tx" purely by whatever transaction is found to have spent the registered withdrawal outpoint in a synced block, then extracts the operator credit by: [3](#0-2) 
using `get_first_op_return_output`, which just calls `.find()` for the first OP_RETURN script in output order: [4](#0-3) , and `parse_op_return_data`, which trusts the first data push after `OP_RETURN` with no validation that it is the *intended* commitment output: [5](#0-4) .

Because only input0/output0 are signature-committed, an attacker (unprivileged, capable only of broadcasting Bitcoin transactions and paying fees) who observes an operator's honest, not-yet-confirmed payout transaction can copy input0 (with its valid signature) and output0 verbatim into a new transaction, then freely choose and order the remaining outputs — e.g. `[output0, decoy_OP_RETURN, real_operator_OP_RETURN, anchor]`. Since the payout transaction's own fee is zero and fee-bumping is done via the anyone-spendable P2A anchor output (`anchor_output`, `NON_EPHEMERAL_ANCHOR_AMOUNT`) [6](#0-5) , the attacker can attach their own higher-fee CPFP child to get their variant mined first (or first if it wins the mempool/mining race), permanently occupying the withdrawal_utxo outpoint before the operator's real broadcast confirms.

Once mined, `update_finalized_payouts` records whatever the decoy OP_RETURN parses to:
- If the decoy fails to parse as a 32-byte xonly pubkey, `operator_xonly_pk` becomes `None`, and `Verifier::is_kickoff_malicious` treats any subsequent honest kickoff by the real funding operator as malicious because the DB has no operator pk to match against: [7](#0-6) .
- If the decoy is a valid 32-byte value equal to a different operator B's xonly pubkey, the DB records B as the payer even though B funded nothing, and `is_kickoff_malicious`'s only check is pk equality with `kickoff_data.operator_xonly_pk`: [8](#0-7) , which B can satisfy by kickoff to claim reimbursement it never funded.

No existing guard (`is_kickoff_malicious`, `SECP.verify_schnorr` on the user signature, or the payout-tx lookup) validates that the confirmed payout transaction is exactly the operator-authored 3-output template (`[payout, anchor, OP_RETURN]`) or that there is exactly one OP_RETURN output; they only check pk equality and the committed blockhash, both of which the attacker's substitute transaction can satisfy trivially.

### Impact Explanation
This directly matches two Critical categories:
- "an operator reimbursed for a payout it never funded" — a colluding/malicious operator B can have themselves recorded as the payer of a withdrawal actually funded by honest operator A's signed input/output0, then claim the kickoff/reimbursement chain for a payout they never funded.
- "an honest operator permanently unable to be reimbursed" — if the decoy is unparseable, the DB permanently records `None` for the payer, and `is_kickoff_malicious` will flag the real funding operator's kickoff as malicious, blocking reimbursement (via `send_asserts`'s hard failure when `payout_op_xonly_pk` is `None`, [9](#0-8) ).

The blast radius is per-withdrawal and repeatable across every withdrawal/deposit and every operator, since it only depends on the generic `SinglePlusAnyoneCanPay` signing scheme used for all payout transactions, not any withdrawal-specific secret.

### Likelihood Explanation
The attacker only needs to observe a broadcast (mempool or already-mined, since the signature is reusable regardless) payout transaction, which is public information, and must win a fee/mining race to get their variant confirmed instead of the original — feasible given the payout tx's own fee is zero and CPFP is used, meaning any party can bump fees on a competing spend of the same input via the anyone-can-spend P2A anchor. No verifier, operator, or aggregator privilege is required; only BTC for fees. This is realistically exploitable but requires timing (racing the original tx to confirmation), which is a moderate but not prohibitive precondition.

### Recommendation
Require the payout transaction template to be fully committed by the signature (e.g. sign with `SIGHASH_ALL` or otherwise cover all outputs, or have the aggregator/verifiers co-sign the full output set), and/or have `update_finalized_payouts`/`is_kickoff_malicious` validate that the confirmed spending transaction matches the exact canonical payout template (output count, order, and script types) rather than trusting the first OP_RETURN found via `.find()`.

### Proof of Concept
```
#[tokio::test]
async fn test_decoy_op_return_hijacks_payout_credit() {
    // 1. Set up regtest bridge, deposit, and a withdrawal_utxo registered for operator A.
    // 2. Have operator A construct (but not yet confirm) its real payout tx via
    //    create_payout_txhandler(input_utxo, output_txout, A_xonly_pk, user_sig, network),
    //    verifying user_sig.sighash_type == SinglePlusAnyoneCanPay.
    // 3. Attacker builds a competing tx reusing the same input0 (same signature) and output0,
    //    but with outputs ordered [output0, decoy_OP_RETURN(unrelated 32 bytes OR operator B's pk),
    //    real_A_OP_RETURN, anchor], and attaches its own CPFP child to get mined first.
    // 4. Mine attacker's tx; run update_finalized_payouts via the verifier's citrea sync path.
    // 5. Call db.get_payout_info_from_move_txid(...) and assert:
    //    assert_ne!(payout_info.0, Some(A_xonly_pk)); // binding broken: real funder not credited
    //    assert_eq!(payout_info.0, Some(B_xonly_pk)); // or None, depending on decoy content
    // 6. Call Verifier::is_kickoff_malicious for A's real kickoff and assert it returns true
    //    (honest operator wrongly flagged malicious), demonstrating the broken binding.
}
```

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

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L1887-1890)
```rust
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L608-617)
```rust
/// Parses the OP_RETURN data from a Bitcoin script. It retrieves the first data push after an OP_RETURN.
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L688-692)
```rust
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```
