### Title
Payout OP_RETURN operator attribution is not covered by the user's SinglePlusAnyoneCanPay signature, allowing a race to forge or erase the credited payer - ([File: core/src/operator.rs], [File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`Operator::withdraw` verifies the user's `in_signature` only against a sighash computed with `SinglePlusAnyoneCanPay` (input 0 / output 0), then builds `payout_txhandler` with an operator-chosen OP_RETURN (`operator_xonly_pk`) and lets `fund_raw_transaction` add arbitrary funding inputs. Because none of the funding inputs, the OP_RETURN output, or the anchor output are covered by the user's signature, anyone possessing `in_signature` (the withdrawing user themselves, or anyone the params were disclosed to) can build and broadcast a competing, self-funded payout transaction with the same input 0/output 0 but an arbitrary OP_RETURN, racing every operator's own `fund_raw_transaction`/`send_raw_transaction` call.

### Finding Description
Binding claimed: `payout_payer_operator_xonly_pk[idx] == operator_who_actually_funded_and_broadcast_the_confirmed_payout_tx[idx]`, and this should coincide with whichever operator's wallet paid out the withdrawal, so that a Reimburse path always exists for the operator that truly funded it.

Trace:
- `Operator::withdraw` (`core/src/operator.rs:630-637`) computes `sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` and calls `SECP.verify_schnorr(&in_signature.signature, &Message::from_digest(*sighash.as_byte_array()), user_xonly_pk)`. With `SinglePlusAnyoneCanPay`, this sighash commits only to input 0 and output 0 — it does **not** commit to output 1 (anchor) or output 2 (`op_return_txout` carrying `operator_xonly_pk`, see `create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`), nor to any additional funding inputs added later by `fund_raw_transaction` with `add_inputs: Some(true)` (`core/src/operator.rs:652-673`).
- Consequently, the *only* thing cryptographically bound by the user is: "input `in_outpoint` pays `out_script_pubkey`/`out_amount` at output index 0." Everything else in the eventually-confirmed transaction — which operator gets attributed the payout, and who funds/broadcasts it — is unauthenticated and freely chosen by whoever gets a transaction using that witness mined first.
- Attribution of "who paid" is derived purely from the confirmed chain transaction: `update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) scans the payout tx that actually spends the withdrawal UTXO, extracts `operator_xonly_pk` from the first OP_RETURN output via `parse_op_return_data`, and if it isn't a valid 32-byte xonly pubkey sets `payout_payer_operator_xonly_pk = NULL`. This is the sole source of truth later consulted by `Operator::validate_payer_is_operator` (`core/src/operator.rs:1686-1740`) and `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`).
- Exploit: an attacker who knows `in_signature`, `in_outpoint`, `out_script_pubkey`, `out_amount` (which is exactly the `WithdrawParams` fanned out by `Aggregator::withdraw` to every operator, or which the withdrawing user trivially possesses since they signed it) constructs their own transaction spending `in_outpoint` with the identical signed input/output-0 pair, self-funds extra inputs, and either (a) puts a valid xonly pubkey belonging to an operator that never funded anything into the OP_RETURN, or (b) omits/corrupts the OP_RETURN entirely, then broadcasts it before any operator's `fund_raw_transaction`→`sign_raw_transaction_with_wallet`→`send_raw_transaction` sequence (`core/src/operator.rs:652-689`) lands. Since the withdrawal UTXO can only be spent once, whichever transaction confirms first wins; the honest operator's own broadcast then fails (input already spent) and returns `Err` with no on-chain trace, and `withdraw()` provides no fallback for the operator to later assert "I intended to pay."
- None of the existing guards catch this: `SECP.verify_schnorr` only checks input 0/output 0 as noted; `is_profitable` only checks amounts; `is_kickoff_malicious` and `update_finalized_payouts` trust whatever OP_RETURN bytes appear on-chain, they don't verify the OP_RETURN was produced by the same party who funded the transaction; there is no DB uniqueness constraint preventing this because `payout_payer_operator_xonly_pk` is written from chain-scan data, not from any authenticated claim.

### Impact Explanation
Two concrete divergences follow directly from the same root cause:
- If the attacker inserts a real operator's xonly pubkey into the forged OP_RETURN while funding the payout themselves, that operator's own automation (`validate_payer_is_operator`) will see itself as `payer_xonly_pk` and proceed to kick off and claim reimbursement for a payout it never funded — "an operator reimbursed for a payout it never funded."
- If the attacker corrupts/omits the OP_RETURN, `payout_payer_operator_xonly_pk` becomes `NULL`, `is_kickoff_malicious` treats any kickoff for that deposit as malicious, and the operator that would legitimately have funded the payout (and lost its own `fund_raw_transaction` race) has no reachable path to prove it intended to pay and claim reimbursement — "an honest operator permanently unable to be reimbursed."

Both outcomes are enumerated Critical-severity categories. The attack is repeatable against every withdrawal broadcast by `Aggregator::withdraw` and against every operator in the fan-out list, since the flaw is structural (missing sighash coverage), not tied to a specific deposit or operator key.

### Likelihood Explanation
The attacker only needs: knowledge of a pending withdrawal's `in_signature`/`in_outpoint`/`out_script_pubkey`/`out_amount` (trivially available to the withdrawing user themselves, and potentially observable by anyone the aggregator or operator disclosed `WithdrawParams` to), the ability to fund and broadcast a Bitcoin transaction with sufficient fee to win a mempool/mining race against the targeted operator's own funding transaction, and standard Bitcoin fee-bumping capability (RBF/CPFP) to outrace the operator. No key compromise, no verifier/operator privilege, and no majority hashrate are required — only fee competition on an unconfirmed UTXO spend, which is well within an "unprivileged attacker who can broadcast transactions and pay fees."

### Recommendation
Bind the payout transaction's OP_RETURN (operator attribution) and any change/output structure into what the user signs, or otherwise cryptographically tie payer attribution to the actual funding source rather than to unauthenticated OP_RETURN bytes scraped off-chain. Concretely: (1) have the user co-sign a full-transaction-committing sighash (e.g. `All` or a dedicated commitment) that includes the OP_RETURN output, or (2) require operators to reserve/lock the input via their own presigned/committed intent (e.g., a DB-level claim with a short exclusivity window, verified before broadcast) so a race cannot silently reassign or erase attribution, and (3) make `is_kickoff_malicious`/`update_finalized_payouts` require an operator-signed attestation (not just a raw OP_RETURN xonly pubkey) proving the funding wallet belongs to that operator.

### Proof of Concept
```rust
// cargo test race_payout_attribution --test deposit_and_withdraw_e2e -- --nocapture
// 1. Set up a deposit and a withdrawal exactly as in
//    core/src/test/deposit_and_withdraw_e2e.rs (generate_withdrawal_transaction_and_signature),
//    obtaining (withdrawal_utxo, payout_txout, sig).
// 2. Build the legitimate operator's payout tx via
//    builder::transaction::create_payout_txhandler(input_utxo, payout_txout.clone(),
//        operator0_xonly_pk, sig, network) and DO NOT broadcast it yet.
// 3. Build an attacker-controlled competing tx spending the same `withdrawal_utxo`
//    with the same witness (sig) at input 0 / output 0 = payout_txout, but with:
//      - an attacker-funded extra input,
//      - a forged OP_RETURN containing operator1_xonly_pk.serialize() bytes
//        (an operator who is NOT operator0).
//    Broadcast this attacker tx first with a higher fee rate.
// 4. Call operator0.withdraw(WithdrawParamsWithSig{...}) — assert it now fails
//    (input already spent) or its fund_raw_transaction/send_raw_transaction errors out.
// 5. Mine blocks, let verifier's update_finalized_payouts (core/src/verifier.rs)
//    process the confirmed attacker tx; query
//    db.get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id
//    and assert payer_xonly_pk == operator1_xonly_pk (Critical: operator1 credited
//    for a payout it never funded), while operator0 has no db state
//    and Operator::validate_payer_is_operator(operator0) returns an error
//    ("Payer info not found") proving operator0's Reimburse path is now
//    permanently unreachable for that withdrawal idx.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L652-691)
```rust
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;

        Ok(signed_tx)
```

**File:** core/src/operator.rs (L1692-1729)
```rust
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
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

**File:** core/src/rpc/aggregator.rs (L1870-1886)
```rust
        let operators = self
            .get_operator_clients()
            .iter()
            .zip(current_operator_xonly_pks.into_iter());
        let withdraw_futures = operators
            .filter(|(_, xonly_pk)| {
                // check if operator_xonly_pks is empty or contains the operator's xonly public key
                operator_xonly_pks_from_rpc.is_empty()
                    || operator_xonly_pks_from_rpc.contains(xonly_pk)
            })
            .map(|(operator, operator_xonly_pk)| {
                let mut operator = operator.clone();
                let params = withdraw_params_with_sig.clone();
                let mut request = Request::new(params);
                request.set_timeout(WITHDRAWAL_TIMEOUT);
                async move { (operator.withdraw(request).await, operator_xonly_pk) }
            });
```
