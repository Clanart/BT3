### Title
Unvalidated first-OP_RETURN parsing in `update_finalized_payouts` lets a malleated payout-clone with a decoy OP_RETURN misattribute (or erase) the funding operator's identity for a withdrawal - (File: core/src/verifier.rs)

### Summary
`get_first_op_return_output` and `parse_op_return_data`, used inside `Verifier::update_finalized_payouts`, blindly take the first OP_RETURN output of whatever transaction is found spending the tracked withdrawal outpoint, with no check that the transaction has the canonical single-OP_RETURN payout structure. Because the withdrawal's spending signature (`user_sig`) is supplied by the withdrawing party who also controls its sighash flag, a malleated variant of the real payout transaction can be constructed with an extra, attacker-chosen OP_RETURN placed before the genuine operator OP_RETURN, corrupting the `payout_payer_operator_xonly_pk` value recorded for that withdrawal index.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` (recorded for withdrawal index `i`) == the x-only pubkey of the operator whose OP_RETURN commitment is in the transaction that actually pays out withdrawal `i`.

`Verifier::update_finalized_payouts` locates the mined transaction that spends the tracked withdrawal UTXO, then does: [1](#0-0) 
This calls `get_first_op_return_output`, defined as: [2](#0-1) 
which uses `Iterator::find` and returns only the *first* OP_RETURN output — there is no check that the transaction contains exactly one OP_RETURN, nor that the transaction otherwise matches the canonical payout structure produced by `create_payout_txhandler` (input: user's withdrawal UTXO/`user_sig`; outputs: user payout, anchor, single OP_RETURN with operator xonly pk): [3](#0-2) 

The signature that authorizes spending the withdrawal input (`user_sig`) is committed via a Taproot key-path spend and is provided by the withdrawing party together with its sighash flag (an explicitly named attacker capability). If that sighash type does not commit to the full output set (e.g. any `ANYONECANPAY`/`SINGLE`/`NONE` variant), the signed witness remains valid for a malleated transaction that keeps input 0/output 0 unchanged but appends extra outputs — including a decoy OP_RETURN inserted *before* the genuine operator OP_RETURN. Whoever gets such a malleated clone mined (instead of the canonical payout tx, since both compete to spend the same UTXO) causes `update_finalized_payouts` to record the decoy's data (invalid pubkey → `None`, or a substituted xonly pk) as the payer for that withdrawal index.

This corrupted attribution then feeds `Verifier::is_kickoff_malicious`, which explicitly treats a missing or mismatched operator pk as malicious: [4](#0-3) 
An honest operator who actually fronted the withdrawal (or whose pubkey appears only in the *second* OP_RETURN of the malleated clone) is then flagged malicious by `handle_kickoff`, and the corresponding on-chain Challenge transaction path can be triggered against them: [5](#0-4) 

No existing guard inspects OP_RETURN count/uniqueness or re-derives/validates the sighash coverage of `user_sig` before this DB write; `verify_storage_proofs`/SPV logic (used in the offline bridge circuit, not in this online DB-update path) is not invoked here at all.

### Impact Explanation
If exploited, the honest operator that genuinely funded a payout can be permanently unable to be reimbursed and/or wrongly flagged malicious (triggering an unwarranted Challenge against them), matching the Critical category "an honest operator permanently unable to be reimbursed" / "wrongly flagged malicious." The corruption is per-withdrawal-index and repeatable across every withdrawal whose payout transaction's authorizing signature uses a malleable sighash flag, so the blast radius scales with the number of withdrawals processed under such a flag, independent of which operator eventually fronts the payout.

### Likelihood Explanation
Exploitability strictly depends on whether the codebase actually permits/validates a malleable sighash type for `user_sig` in the withdrawal flow (i.e., something other than `SIGHASH_ALL`/`SIGHASH_DEFAULT` covering the whole output set), and on the attacker being able to race their decoy-appended clone into a block ahead of the canonical payout transaction consuming the same outpoint. I could not, within the available index, locate a check anywhere in the deposit/withdrawal setup path (`Verifier::is_deposit_valid` or its callers) that constrains `in_signature.sighash_type` to a non-malleable value before it is accepted as the authorizing withdrawal signature; this is a genuine gap that I was unable to fully rule out or confirm due to the size/scope of the indexed code. Feasibility otherwise only costs normal Bitcoin transaction fees and requires no special role beyond the explicitly-granted unprivileged attacker capabilities (crafting the withdrawal UTXO/signature/sighash flag and broadcasting transactions).

### Recommendation
- In `update_finalized_payouts`, reject (treat as no valid attribution / do not overwrite) any mined "payout" transaction whose output structure does not exactly match the canonical payout shape (exactly one OP_RETURN output, at the expected index, with the expected script push length), rather than silently taking `find()`'s first match.
- Enforce that the withdrawal-authorizing `user_sig` sighash type is `SIGHASH_ALL`/`SIGHASH_DEFAULT` (covering all outputs) at the point the withdrawal signature is accepted, so no additional outputs (including decoy OP_RETURNs) can be appended without invalidating the signature.

### Proof of Concept
```rust
// cargo test in core/src or circuits-lib exercising get_first_op_return_output/parse_op_return_data
#[test]
fn payout_clone_multiple_op_return_misattribution() {
    // 1. Build the canonical honest payout tx via create_payout_txhandler:
    //    input0 = withdrawal UTXO, output0 = user payout, output1 = anchor,
    //    output2 = OP_RETURN(honest_operator_xonly_pk).
    // 2. Clone it, keeping input0 and output0 byte-identical (and the same
    //    witness/user_sig, assuming a malleable sighash flag), but append an
    //    extra OP_RETURN(attacker_garbage_or_other_pk) BEFORE the honest
    //    operator's OP_RETURN, i.e. outputs = [payout, anchor,
    //    OP_RETURN(garbage), OP_RETURN(honest_operator_xonly_pk)].
    // 3. Assert the witness for input0 still verifies against the clone's
    //    sighash (proving on-chain validity under the chosen sighash flag).
    // 4. Call get_first_op_return_output(&clone) and parse_op_return_data on it.
    // 5. Assert the parsed pubkey != honest_operator_xonly_pk (either None
    //    or the garbage value), demonstrating that the binding
    //    "recorded payer pk == actual funding operator" is broken.
}
```

### Citations

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

**File:** core/src/verifier.rs (L1969-2017)
```rust
    pub async fn handle_kickoff<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        kickoff_witness: Witness,
        mut deposit_data: DepositData,
        kickoff_data: KickoffData,
        challenged_before: bool,
    ) -> Result<bool, BridgeError> {
        let is_malicious = self
            .is_kickoff_malicious(kickoff_witness, &mut deposit_data, kickoff_data, dbtx)
            .await?;

        let deposit_outpoint = deposit_data.get_deposit_outpoint();

        let (_signed_txs, tx_metadata, challenge_tx) = self
            .get_signed_txs_for_kickoff(dbtx, kickoff_data, deposit_data)
            .await?;

        if is_malicious {
            tracing::warn!(
                "Malicious {} detected. {} Challenge tx: {} for deposit {}",
                kickoff_data,
                match challenged_before {
                    false => "This is the first malicious kickoff in the current round.",
                    true => "This is not the first malicious kickoff in the current round.",
                },
                bitcoin::consensus::encode::serialize_hex(&challenge_tx),
                deposit_outpoint
            );
            // do not automatically send challenge txs on mainnet or testnet4
            if !challenged_before
                && !matches!(
                    self.config.protocol_paramset().network,
                    bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
                )
            {
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L688-692)
```rust
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
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
