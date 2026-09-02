### Title
Payout OP_RETURN operator attribution is unsigned and malleable, allowing anyone to redirect reimbursement credit to an arbitrary operator - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` builds the payout transaction with a single input signed only by the withdrawing user, using a mandatory `TapSighashType::SinglePlusAnyoneCanPay` signature that never commits to the OP_RETURN output holding the fronting operator's xonly pubkey. Anyone who observes a broadcast (unconfirmed) payout transaction can rebroadcast a modified version with the identical signed input/output0 but a different OP_RETURN naming any other registered operator, and if that version confirms, `Verifier::update_finalized_payouts` will attribute the payout to the wrong operator, letting that operator's automated `payout_checker` task claim reimbursement it never funded while the true payer is permanently uncreditable.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` recorded in `withdrawals` for withdrawal `i` (read by `get_first_unhandled_payout_by_operator_xonly_pk` and used to drive automatic reimbursement) must equal the operator who actually fronted/broadcast/paid the fee for withdrawal `i`'s payout. This binding is not enforced by any signature.

Root cause:
- `create_payout_txhandler` [1](#0-0)  constructs the payout tx with one input (the withdrawal UTXO) spent via `SpendPath::KeySpend` using only the user's `taproot::Signature`, and adds an OP_RETURN output (`op_return_txout`) encoding `operator_xonly_pk.serialize()` — a value the user's signature does not authenticate for any sighash type that excludes it.
- The RPC layer forces the user's signature to be `TapSighashType::SinglePlusAnyoneCanPay` [2](#0-1) . Under BIP341/BIP143 semantics, `SIGHASH_SINGLE` commits only to the output at the *same index* as the signed input (output 0, the user payout output) and `ANYONECANPAY` excludes commitments to other inputs — it never commits to output index 2 (the OP_RETURN). This is exactly the reason the proto documents the sighash type explicitly: "User's signature is only valid for this [output] pubkey/amount" — for output 0 only [3](#0-2) .
- `Verifier::update_finalized_payouts` blindly trusts whatever OP_RETURN ends up in the confirmed block's transaction to attribute the payout to an operator, with no cross-check against who actually broadcast/paid fees for it [4](#0-3) .
- `PayoutCheckerTask::run_once` (per-operator background task) automatically pulls "its" unhandled payout via `get_first_unhandled_payout_by_operator_xonly_pk` and calls `handle_finalized_payout`/`end_round` to claim reimbursement, with no manual review step [5](#0-4) .
- `is_kickoff_malicious`, the only verifier-side guard tying kickoff to payout, only checks that the DB-recorded `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` [6](#0-5)  — it never re-derives who actually paid the fee/broadcast the payout tx, so it cannot detect the swap.

Exploit flow: Operator O1 broadcasts a real payout tx for withdrawal `i` (public in mempool, signed input0 + output0 under SIGHASH_SINGLE|ANYONECANPAY, OP_RETURN = O1's pk). The attacker copies input0's witness and output0 verbatim (valid since unmodified), attaches extra fee-paying inputs/outputs of their own (permitted by ANYONECANPAY), and swaps the OP_RETURN to O2's public xonly pk. This new transaction is a valid, differently-signed conflicting spend of the same withdrawal UTXO; if it confirms instead of O1's original (via higher fee/RBF-style replacement), `update_finalized_payouts` records `payout_payer_operator_xonly_pk = O2` for withdrawal `i`. O2's own `payout_checker` task will then automatically claim reimbursement for a withdrawal it never funded, while O1 (the real payer) can never be found by any operator's `get_first_unhandled_payout_by_operator_xonly_pk` lookup and is permanently unreimbursed for `i`.

### Impact Explanation
This is Critical impact under "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed." O2 is credited (and later reimbursed via the kickoff/reimburse transaction graph) for a bridge payout it never funded, meaning BTC value from the move-to-vault UTXO cycle flows to an operator beyond what it fronted, while O1 who genuinely paid the withdrawing user out of pocket has no recorded claim and is permanently excluded from reimbursement for that deposit. The attack is repeatable for every unconfirmed payout transaction across any deposit/operator pair, since it depends only on public information (mempool contents, operators' public xonly pks) and standard transaction malleability enabled by the mandatory `SinglePlusAnyoneCanPay` sighash choice.

### Likelihood Explanation
The attacker needs no privileged role: only the ability to observe the Bitcoin mempool, construct a conflicting transaction reusing a public witness, and pay a competitive fee to win the race before O1's original transaction confirms — capabilities explicitly granted to unprivileged attackers in scope. The main practical requirement is winning the transaction-replacement race (fee-bumping/RBF), which is feasible and cheap relative to the withdrawal amount being redirected. It only requires one unconfirmed operator-fronted payout tx to exist in the mempool at any point, a condition that occurs routinely in normal operation.

### Recommendation
Bind the OP_RETURN operator identity to a value that is cryptographically committed by the fronting operator, e.g. require the operator to co-sign the payout transaction (adding an operator-controlled input/signature covering all outputs including the OP_RETURN via SIGHASH_ALL), or have verifiers/aggregator additionally verify, before crediting a payout, that the crediting operator's own key/signature authorized that specific OP_RETURN content (not merely that it matches the kickoff). Alternatively, redesign attribution to not rely on a mutable/unauthenticated OP_RETURN at all.

### Proof of Concept
`cargo test` plan (core/src/verifier.rs / core/src/database/verifier.rs test module):
1. Set up two withdrawals `i` (move_to_vault_txid_i) and `j` (move_to_vault_txid_j) with distinct `withdrawal_utxo`s in the same block, and two operator xonly pks `O1`, `O2`.
2. Construct payout tx `payout_tx_i` spending withdrawal `i`'s UTXO with a valid `SinglePlusAnyoneCanPay` witness on input 0 and OP_RETURN = `O1`'s pk (simulating the real fronting op).
3. Construct payout tx `payout_tx_i_swapped` reusing the identical input0 witness/output0 from `payout_tx_i`, but with OP_RETURN = `O2`'s pk plus an extra attacker fee input/output — assert it still passes taproot signature verification for input0 (`SECP.verify_schnorr` against the SIGHASH_SINGLE|ANYONECANPAY sighash, which excludes output index 2).
4. Feed `payout_tx_i_swapped` (instead of `payout_tx_i`) and the honest `payout_tx_j` (OP_RETURN = `O2`, genuine) into a constructed `BlockCache`/block, and call `Verifier::update_finalized_payouts`.
5. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(O2)` returns withdrawal `i` **and**, after marking it handled, also returns withdrawal `j` (double credit).
6. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(O1)` returns `None` (zero credit) even though O1 signed/broadcast the real fee-paying transaction for `i`.

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

**File:** core/src/verifier.rs (L2312-2343)
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
        }
```

**File:** core/src/task/payout_checker.rs (L39-106)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
