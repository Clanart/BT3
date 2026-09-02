This confirms the finding: `validate_payer_is_operator` (core/src/operator.rs:1687-1740) only checks that `payer_xonly_pk == self.signer.xonly_public_key` — i.e., whichever key ended up in `payout_payer_operator_xonly_pk` — with no on-chain proof that this key's holder actually funded `output_txout.value`. Similarly `is_kickoff_malicious` (core/src/verifier.rs:1857-1915) and `send_asserts` (core/src/operator.rs:1284-1295) both only compare the *recorded* `operator_xonly_pk` against the kickoff's claimed operator, never against the actual source of BTC that funded the payout output. [1](#0-0) and [2](#0-1)  show the payout tx has input 0 = the user's dust withdrawal UTXO (signed with `SinglePlusAnyoneCanPay`), and the OP_RETURN output containing `operator_xonly_pk` is a plain, unsigned, uncommitted output appended after the sighash-covered output. Because `SIGHASH_SINGLE | ANYONECANPAY` only commits input 0 and the output at the same index (the user's payout output), any party holding the signature can build a brand-new transaction that reuses input 0, keeps the committed payout output identical, but funds it with their **own** additional inputs and substitutes an arbitrary OP_RETURN — pointing at any real operator's `xonly_pk`, not their own. [3](#0-2)  (`update_finalized_payouts`) then blindly trusts whatever OP_RETURN ends up in the confirmed spending transaction and stores it as `payout_payer_operator_xonly_pk`. [4](#0-3)  shows each operator's `PayoutCheckerTask` polls `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` and automatically drives the kickoff/reimbursement flow for any payout whose DB-recorded payer matches its own key — with no verification of actual on-chain funding.

### Title
Payout OP_RETURN operator identity is unauthenticated, allowing payout credit theft between operators - (core/src/verifier.rs::update_finalized_payouts)

### Summary
`update_finalized_payouts` extracts `operator_xonly_pk` purely from the OP_RETURN of whichever transaction happens to spend the tracked withdrawal UTXO on-chain, with no cryptographic binding between that value and the party who actually supplied the funds for `output_txout`. Because the payout's authorizing signature uses `SIGHASH_SINGLE | ANYONECANPAY`, only the user's small input and the fixed payout output are committed; the OP_RETURN (and the extra funding inputs) are unauthenticated and freely substitutable by anyone who has seen the signature.

### Finding Description
The broken binding: `operator_xonly_pk` (parsed from the confirmed payout tx's OP_RETURN, `core/src/verifier.rs:2319-2321`) is asserted to equal "the operator who funded `output_txout.value`," but nothing in the code enforces this equality.

- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-435`) builds: input[0] = user's withdrawal UTXO (keypath-signed by the user with `SinglePlusAnyoneCanPay`), output[0] = `output_txout` (user's payout, committed by SIGHASH_SINGLE), output[1] = anchor, output[2] = OP_RETURN(`operator_xonly_pk`) — uncommitted by the signature.
- `Operator::withdraw` (`core/src/operator.rs:620-637`) verifies only that the user's signature is valid over input 0 / output 0; the additional funding inputs are added later via `fund_raw_transaction` and are not tied cryptographically to `operator_xonly_pk`.
- Because ANYONECANPAY|SINGLE leaves all outputs after index 0 and all inputs after index 0 unsigned/uncommitted, any party who obtains the user's signature (which the withdrawing user itself possesses, or which can be sniffed from mempool/gRPC broadcasts) can build a competing transaction: same input 0, same output 0 (so the withdrawal is still honestly fulfilled), but its own funding inputs (paying `output_txout.value` out of its own pocket) and an arbitrary OP_RETURN naming any real registered operator's `xonly_pk` — including one that never funded anything.
- `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) picks up whichever transaction actually confirms and mechanically records that OP_RETURN value as `payout_payer_operator_xonly_pk` in the `withdrawals` table, with zero cross-check against who supplied the additional inputs.
- Downstream, `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-47`) and `validate_payer_is_operator` (`core/src/operator.rs:1687-1740`) trust this DB value as ground truth: any operator whose key was named wins the automated kickoff/reimbursement flow, and `is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) / `send_asserts` (`core/src/operator.rs:1284-1295`) only check *consistency* of the recorded payer against the kickoff's claimed identity, never *provenance* of the actual BTC spent.

Existing guards fail to close this gap: `SECP.verify_schnorr` in `Operator::withdraw` only authenticates the user's intent for input/output 0, not the OP_RETURN; `is_kickoff_malicious` only compares two untrusted, attacker-influenced fields to each other; there is no on-chain or off-chain signature tying `operator_xonly_pk` to ownership of the extra funding inputs.

### Impact Explanation
An attacker who reuses the leaked SIGHASH_SINGLE|ANYONECANPAY signature can front the honest withdrawal themselves (paying `output_txout.value` from their own wallet, so the end-user is unaffected) while naming a different, real, collateralized operator in the OP_RETURN. That named operator's own automation (`PayoutCheckerTask`) will then autonomously kick off and eventually be reimbursed from the move-to-vault UTXO for BTC it never spent — a Critical-severity "operator reimbursed for a payout it never funded." Simultaneously, the honest operator who actually intended to front the payout (if it broadcasts second, or is outbid via RBF) is permanently unable to be recorded/reimbursed for the funds they spent, since the `withdrawals` row is keyed by withdrawal index/move-txid and only one payer can be recorded. This is repeatable for every withdrawal, against every registered operator whose `xonly_pk` is public knowledge (all operator pubkeys are broadcast to the aggregator/verifiers).

### Likelihood Explanation
The attacker only needs (a) the ability to broadcast Bitcoin transactions and pay fees, and (b) knowledge of the withdrawal's signed input+output — obtainable either by being the withdrawing user (who legitimately holds the signature) or by observing it in an operator's mempool broadcast/gRPC exchange. The attacker's cost is exactly `output_txout.value` (the payout amount) plus fees, which they can recover in some flows depending on race timing, but even without recovering it, the act permanently corrupts DB attribution and steals reimbursement credit for the named operator at the honest operator's expense. No special access, key compromise, or majority hashrate is required — only competitive Bitcoin fee bidding (RBF) to win the block-confirmation race for the shared input.

### Recommendation
Cryptographically bind `operator_xonly_pk` to actual provenance of the payout funding, e.g., require the operator's own Schnorr signature (using their registered `xonly_pk`) over the payout transaction's additional funding input(s) or over a commitment covering the OP_RETURN itself, and validate that signature in `update_finalized_payouts` before trusting the OP_RETURN value. Alternatively, extend the withdrawal signature's sighash coverage (e.g., require `SIGHASH_ALL` for at least the OP_RETURN output, or a separate signed attestation) so the OP_RETURN cannot be swapped without invalidating the user's original authorization.

### Proof of Concept
```
cargo test -p clementine-core update_finalized_payouts_operator_identity_theft
```
Plan:
1. Set up a deposit and withdrawal per `core/src/test/deposit_and_withdraw_e2e.rs` helpers; obtain the user's `SinglePlusAnyoneCanPay` signature over the withdrawal UTXO (as in `sign_withdrawal_output` in `core/src/test/common/setup_utils.rs:499-543`).
2. Have "Operator A" (the honest fronting operator) build and hold (but not yet broadcast) its `create_payout_txhandler` result via `Operator::withdraw`.
3. As the attacker, construct a second transaction reusing the same input 0 and identical output 0 (`output_txout`), but funded by a different wallet UTXO and with the OP_RETURN set to Operator B's `xonly_pk` (a real registered operator who did not fund anything). Broadcast this with higher fee so it confirms instead of A's tx.
4. Run the verifier's block sync so `update_finalized_payouts` processes the block.
5. Assert `db.get_payout_info_from_move_txid(...).0 == Some(operator_B_xonly_pk)` while asserting operator B's on-chain wallet balance did not decrease by `output_txout.value` (the attacker's wallet did instead), demonstrating the credited operator differs from the actual funder — and that operator A (who never got to broadcast, or whose broadcast was replaced) has no path to be credited for this withdrawal.

### Citations

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

**File:** core/src/operator.rs (L620-637)
```rust
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

**File:** core/src/task/payout_checker.rs (L41-47)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```
