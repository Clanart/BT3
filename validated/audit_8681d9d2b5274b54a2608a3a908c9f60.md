### Title
Honest operator's payout wrongly deemed malicious via SIGHASH_SINGLE|ANYONECANPAY malleation stripping the OP_RETURN attribution - (File: core/src/verifier.rs)

### Summary
The payout transaction's only signed input uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits solely to the withdrawal input and the matching output index, leaving the anchor output and the operator-attribution OP_RETURN output completely unsigned. Anyone who observes the operator's broadcast payout tx in the mempool can rebroadcast a fee-bumped variant that reuses the identical signed input/output pair but drops or corrupts the OP_RETURN, and if that variant confirms first, `update_finalized_payouts` records `operator_xonly_pk = None` for the withdrawal, causing `Verifier::is_kickoff_malicious` to treat the honest operator's subsequent kickoff as malicious.

### Finding Description
The binding this code implicitly assumes is: `operator_xonly_pk stored in DB for withdrawal idx (parsed from OP_RETURN of whichever tx confirms spending the withdrawal UTXO) == operator_xonly_pk of the party who actually authorized/funded that payout under the user's signature`.

`create_payout_txhandler` builds the payout tx with three outputs — the user payout (output 0), an anchor (output 1), and an OP_RETURN with the fronting operator's x-only pubkey (output 2) — and signs only via a taproot key-spend witness built from the user-provided `taproot::Signature` (`user_sig`) at input 0: [1](#0-0) 

The proto/RPC layer documents that this user signature uses `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1) 

Under `SIGHASH_SINGLE | ANYONECANPAY`, the signature commits only to the spent input and the output at the same index (output 0, the user's payout) — it does **not** commit to the anchor output, the OP_RETURN output, or any other inputs that might be added to the transaction. This means once the operator broadcasts the payout tx and its witness (the user signature) becomes visible in the mempool, anyone can construct an alternative transaction reusing the exact same input + signature + output 0, while stripping the OP_RETURN and/or adding extra fee-paying inputs (permitted since ANYONECANPAY does not sign other inputs) to outbid the operator's version for confirmation.

On confirmation, `update_finalized_payouts` derives the credited operator solely by parsing the OP_RETURN of whichever transaction actually spent the withdrawal UTXO in that block, with no cross-check against the transaction the operator itself signed/broadcast: [3](#0-2) 

If the malleated (OP_RETURN-less) variant is the one that confirms, `operator_xonly_pk` is stored as `NULL` for that withdrawal index. Later, when the honest operator sends its kickoff to claim reimbursement, `Verifier::is_kickoff_malicious` reads this `None` value and treats it as malicious by design: [4](#0-3) 

This triggers Challenge/Disprove against an operator who genuinely funded the user's payout, because the on-chain attribution mechanism (an unsigned OP_RETURN output) can be detached from the actually-authorized spend by any third party observing the mempool — no compromise of keys, verifiers, or the withdrawal UTXO's private key is required.

Existing guards do not prevent this: `is_kickoff_malicious`'s downstream checks (operator xonly pk match, committed blockhash) only run after the `None` case is already handled as "assume malicious"; there's no reconciliation step that checks whether an operator's own signed/broadcast payout tx (even if replaced by a malleated variant on-chain) is the one that funded the withdrawal.

### Impact Explanation
An honest operator's round collateral is burned via Challenge/Disprove despite having correctly funded the user's withdrawal, matching the Critical category "an honest operator's collateral burned." The attack is repeatable per withdrawal and per operator, since it depends only on public mempool visibility of a payout tx signed with `SinglePlusAnyoneCanPay`, not on any operator- or deposit-specific weakness. Any observer (unprivileged, per the threat model) can mount it against any operator's payout.

### Likelihood Explanation
The precondition is minimal: the attacker needs only to watch the Bitcoin mempool for a broadcast payout transaction (a public, unauthenticated data source), understand that `SinglePlusAnyoneCanPay` leaves the OP_RETURN and anchor unsigned, and pay a bitcoin transaction fee to get their malleated variant mined ahead of the operator's original. No aggregator, verifier, or Citrea access is needed, and no key compromise is required — this is a straightforward and well-known sighash malleability primitive. Feasibility is high given operators must broadcast payout txs publicly before they confirm, and the race only requires modest fee-bumping since the payout tx's own fee is via a CPFP anchor.

### Recommendation
Do not derive operator attribution solely from an unauthenticated, unsigned OP_RETURN output. Instead, commit the fronting operator's x-only pubkey inside the sighash of the user's signed message (e.g., sign the payout tx with `SIGHASH_ALL` semantics over all outputs, or otherwise cryptographically bind the OP_RETURN data to the same signature that authorizes the withdrawal spend), so that any tx spending the withdrawal UTXO with a valid signature necessarily carries the correct, unforgeable operator attribution.

### Proof of Concept
```
cargo test malleated_payout_strips_operator_attribution --package core
```
Test plan:
1. Run a deposit + withdrawal e2e setup (as in `core/src/test/deposit_and_withdraw_e2e.rs`) creating a withdrawal UTXO and obtaining `(input_utxo, output_txout, user_sig)` via `generate_withdrawal_transaction_and_signature`.
2. Have operator 0 call `withdraw`, producing the signed payout tx (`payout_txhandler`) with OP_RETURN output committing `operator_0_xonly_pk`; capture it from the mempool before confirmation.
3. Construct a "decoy" transaction reusing the identical input (`in_outpoint`), the identical witness/signature, and identical output 0 (payout output), but replacing/removing output 2 (OP_RETURN) and adding one additional funding input to raise the total fee (valid since sighash is `SinglePlusAnyoneCanPay`).
4. Broadcast the decoy with higher effective fee so it is mined before the operator's original tx; assert the operator's original tx is now double-spent/rejected.
5. Run the block/citrea sync path (`update_finalized_payouts`) and assert `get_payout_info_from_move_txid` now returns `operator_xonly_pk_opt == None` for this withdrawal index.
6. Call `Verifier::is_kickoff_malicious` for operator 0's subsequent kickoff on this deposit, and assert it incorrectly returns `Ok(true)`, confirming the honest operator is flagged malicious despite having funded (signed) the exact payout output that ultimately reached the user.

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

**File:** core/src/rpc/clementine.proto (L239-253)
```text
message WithdrawParams {
  // The ID of the withdrawal in Citrea
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
}
```

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
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
