### Title
Unauthenticated payout OP_RETURN lets any party assign the reimbursement credit to an arbitrary operator - (File: `core/src/verifier.rs`, `circuits-lib/src/bridge_circuit/mod.rs`)

### Summary
`Verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` purely by taking the **first** OP_RETURN output of the mined payout transaction via `get_first_op_return_output`/`parse_op_return_data`, with no check that the named key actually funded any input of that transaction. Because the withdrawal input is signed by the user with `TapSighashType::SinglePlusAnyoneCanPay` (which only commits to input0 and output0), any party who obtains that signature can build their own competing payout transaction, self-fund the fee/shortfall, and freely choose the OP_RETURN bytes — including a decoy placed before/instead of the genuine operator's commitment.

### Finding Description
The broken binding: `operator_xonly_pk_recorded_for_withdrawal_index == xonly_pk_of_party_whose_funds_actually_paid_the_difference`.

- `create_payout_txhandler` builds output0 = user payout, output1 = anchor, output2 = OP_RETURN(move_txid‖operator_xonly_pk) [1](#0-0) , and the user's input signature is required to be `SinglePlusAnyoneCanPay` [2](#0-1) .
- `SinglePlusAnyoneCanPay` only signs input0 in isolation and output at the same index (output0); it places **no constraint whatsoever** on any other input or output of the final transaction (`Prevouts::One` is used for this sighash type in `calculate_pubkey_spend_sighash`) [3](#0-2) . This is explicitly acknowledged elsewhere in the codebase: the RBF funding code deliberately places the wallet's change output last "so that SinglePlusAnyoneCanPay signatures stay valid" [4](#0-3) , confirming that everything besides output0 is unconstrained by the user's signature.
- `Verifier::update_finalized_payouts` scans the mined transaction, takes the *first* OP_RETURN output, parses it as an `XOnlyPublicKey`, and writes it straight into the `withdrawals` table with no verification that the referenced key signed or funded anything in the transaction [5](#0-4) .
- `Verifier::is_kickoff_malicious` later trusts this DB value unconditionally: it only checks that the recorded `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` and that the committed payout blockhash matches — it never checks that the named operator's key actually appears as a signer/funder of the payout transaction's inputs [6](#0-5) .
- `PayoutCheckerTask` on each operator's own node polls `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` and, if a match is found, automatically drives the reimbursement flow (`handle_finalized_payout`, `end_round`, etc.) [7](#0-6) .

Exploit flow: an unprivileged attacker (who is the withdrawing user, or who has observed a broadcast-but-unconfirmed payout tx and thus knows the `SinglePlusAnyoneCanPay` signature and output0) constructs their own transaction reusing input0 (withdrawal UTXO + user signature) and output0 unchanged, adds their own funding input(s) to cover the fee/shortfall, and appends whichever OP_RETURN bytes they like as the first OP_RETURN (e.g. a genuine, uninvolved operator B's x-only pubkey, optionally followed by the real operator A's OP_RETURN as a second output to match the described "decoy inserted before genuine" pattern). If this transaction gets mined instead of the honest operator's own broadcast, `get_first_op_return_output` picks the decoy, and the DB permanently attributes the payout to operator B. Operator B's own `PayoutCheckerTask` will then autonomously trigger a kickoff/reimbursement for a payout B never funded, and `is_kickoff_malicious` will approve it (`Ok(false)`) because it only checks the (attacker-controlled) DB record and committed blockhash, not on-chain fund provenance.

### Impact Explanation
This is Critical: an operator can be reimbursed via the N-of-N-guarded challenge/reimburse transaction graph for a payout it never funded, because the payer identity used throughout the protocol (`update_finalized_payouts` → `get_payout_info_from_move_txid` → `is_kickoff_malicious` → `PayoutCheckerTask`) is sourced entirely from an unauthenticated OP_RETURN field that any party controlling a `SinglePlusAnyoneCanPay`-signed withdrawal input can set. It is repeatable per withdrawal/deposit and does not depend on which operator is honest — any operator xonly pubkey (or none, causing "optimistic payout"-style griefing of the correct funder) can be injected, and the honest operator who actually incurs the cost may end up permanently unable to be credited for that withdrawal, since `is_payout_handled`/`payout_payer_operator_xonly_pk` are keyed by withdrawal index and get set once.

### Likelihood Explanation
Preconditions are cheap: any user withdrawing (or any party who intercepts the mempool-broadcast, ANYONECANPAY-signed payout tx before confirmation) can build a competing/independent transaction, fund the shortfall out of pocket, and win a normal Bitcoin block race or RBF/mempool race against the honest operator's broadcast. No verifier, aggregator, or operator key compromise is needed; cost is limited to the fee and the (bounded, `is_profitable`-capped) shortfall amount, which the attacker recovers indirectly since the user still receives the withdrawal. This is fully repeatable across every withdrawal and every operator xonly pubkey.

### Recommendation
Do not trust the payout OP_RETURN's operator pubkey as sole proof of who fronted a withdrawal. Bind the credited operator to cryptographic proof that the operator actually supplied on-chain value for the payout — e.g., require and verify that at least one payout-tx input is spent from that operator's known collateral/output-tracking script, or require the operator's own Schnorr signature (verified against a registered operator key, similar to `SECP.verify_schnorr` checks elsewhere) covering the OP_RETURN output/operator identity, rather than parsing arbitrary unauthenticated bytes with `get_first_op_return_output`/`parse_op_return_data`.

### Proof of Concept
```
cargo test -p core --features automation test_payout_credit_hijack_via_decoy_op_return -- --nocapture
```
Test plan:
1. Regtest setup: create a deposit, register the withdrawal UTXO (`update_withdrawal_utxo_from_citrea_withdrawal`), and obtain the withdrawal user's `SinglePlusAnyoneCanPay` signature + output0 exactly as `sign_withdrawal_output`/`generate_withdrawal_transaction_and_signature` do.
2. Instead of calling `Operator::withdraw` for the real operator A, manually construct a raw transaction: input0 = withdrawal UTXO with the user's signature, output0 unchanged, an attacker-funded extra input, output1 = decoy OP_RETURN with operator B's `XOnlyPublicKey` (B never contributed any input), output2 = genuine OP_RETURN with operator A's xonly pk (to mirror the described "decoy before genuine" layout). Sign the attacker input with the attacker's own key and broadcast; mine the block.
3. Run (the equivalent of) `Verifier::update_finalized_payouts` over that block.
4. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(A)` is `None` (A gets no credit despite being the intended funder) and `db.get_first_unhandled_payout_by_operator_xonly_pk(B)` returns `Some(...)` (B is credited despite contributing no input).
5. Assert `Verifier::is_kickoff_malicious` returns `Ok(false)` for a crafted `kickoff_data.operator_xonly_pk == B`, demonstrating B could complete a Reimburse for funds it never fronted.

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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
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

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L161-163)
```rust
            change_address: None,
            change_position: Some(tx.output.len() as u16), // Add change output at last index (so that SinglePlusAnyoneCanPay signatures stay valid)
            change_type: None,
```

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
