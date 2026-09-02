## Title
Payout `OP_RETURN` operator‑attribution output is unsigned under `SinglePlusAnyoneCanPay`, letting anyone attribute a payout to an arbitrary operator - (File: `core/src/builder/transaction/operator_reimburse.rs`, `core/src/rpc/parser/operator.rs`, `core/src/verifier.rs`)

### Summary
The binding claimed in the question — *the `tx_sender` queue's operator‑controlled `TransactionType::Payout` path lets a second operator poison another operator's queue* — is a dead end: `add_tx_to_queue` (`core/src/tx_sender_queue.rs:92-105`) is an internal Rust API invoked only from already-privileged operator/verifier/aggregator code paths (`core/src/operator.rs`, `core/src/rpc/aggregator.rs`), never from an unauthenticated gRPC surface. The reachable variant is real, however: the mandatory `SinglePlusAnyoneCanPay` sighash enforced on the user's withdrawal signature (`core/src/rpc/parser/operator.rs:174-187`) does not cover the payout tx's `OP_RETURN` output that names the fronting operator, so anyone possessing that signature (including the withdrawing user themself) can build and broadcast an alternate payout tx naming a different operator, and `update_finalized_payouts` will attribute it purely from the mined bytes.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` for withdrawal index `i` == the xonly pk of the party whose funds actually paid output 0 of the mined payout tx.

`create_payout_txhandler` builds: input 0 = user's withdrawal UTXO (key‑spend), output 0 = user payout, output 1 = anchor, output 2 = `op_return_txout(operator_xonly_pk)` [1](#0-0) . The only signature covering input 0 is the user's, and `parse_withdrawal_sig_params` hard‑enforces `TapSighashType::SinglePlusAnyoneCanPay` [2](#0-1) . `SIGHASH_SINGLE | ANYONECANPAY` only commits the signer to: (a) the single input being spent, and (b) the single output at the *same index* as that input (output 0). It does **not** commit to any other input or any other output — in particular not to the anchor output or the `OP_RETURN` output carrying `operator_xonly_pk`.

`Operator::withdraw` verifies exactly this sighash and nothing more before funding the tx via `fund_raw_transaction` (which only adds *additional*, unconstrained inputs/change) [3](#0-2) . Consequently, given a valid user `(in_signature, in_outpoint, output_script_pubkey, output_amount)` tuple — which the withdrawing user (the unprivileged attacker per the threat model) constructs themselves when calling `withdraw` on the Citrea Bridge contract — an attacker can independently build a competing raw payout transaction that: reuses input 0 and output 0 unchanged (keeping the signature valid), and sets the `OP_RETURN` output to name an arbitrary victim operator's xonly pubkey instead of the real fronting operator's. The attacker funds/completes and broadcasts this transaction directly to the Bitcoin network (the anchor output is a pay‑to‑anyone P2A output, so anyone can CPFP‑bump it), entirely bypassing `add_tx_to_queue`/`tx_sender`.

Once mined, `Verifier::update_finalized_payouts` scans the block, extracts the first `OP_RETURN` of the mined payout tx, and records whatever xonly pubkey is present as `payout_payer_operator_xonly_pk` — with no check that this operator actually supplied the funding inputs of that transaction [4](#0-3) . This value later drives `get_first_unhandled_payout_by_operator_xonly_pk`, which automatically feeds an operator's own kickoff/reimbursement automation for payouts attributed to their key [5](#0-4) . None of the guards listed in the audit rubric (`is_deposit_valid`, `is_profitable`, `SECP.verify_schnorr`, storage-proof/SPV checks, or the bridge circuit) validate that the named operator actually funded the extra input(s); `bridge_circuit` also only reads the `OP_RETURN` bytes verbatim as `operator_xonlypk` [6](#0-5) .

### Impact Explanation
An arbitrary, unprivileged party can cause an uninvolved operator to be recorded as the payer of a withdrawal it never funded. Once recorded, that operator's own automated kickoff/reimburse flow will surface the withdrawal via `get_first_unhandled_payout_by_operator_xonly_pk` and proceed to claim bridge reimbursement for BTC it never sent — draining the bridge's collateral/vault for a fictitious front. This matches the Critical category "an operator reimbursed for a payout it never funded." It is repeatable per withdrawal (any withdrawing user can retarget attribution to any registered operator) and is not limited to a single deposit or operator pair.

### Likelihood Explanation
No special privileges are required beyond being a normal bridge user who requests a withdrawal on Citrea and can broadcast Bitcoin transactions/pay fees — squarely within the defined unprivileged attacker capability set ("choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag ... send requests to the aggregator's public gRPC port"). The mandated sighash type (`SinglePlusAnyoneCanPay`) is precisely the malleable case that leaves the `OP_RETURN` attribution output unsigned; the attacker's own withdrawal signature suffices, no key compromise or majority hashrate needed. Cost is limited to Bitcoin mining fees plus the ordinary funding of their own withdrawal output (they receive that value back to themselves).

### Recommendation
Bind the operator attribution to the signed payload: either (a) require the user signature to use `TapSighashType::All` (or `AllPlusAnyoneCanPay`) so every output including `OP_RETURN` is committed, or (b) have the aggregator/user commit to the specific operator's xonly pk as part of the Citrea withdrawal request/storage proof, and verify in `update_finalized_payouts`/`bridge_circuit` that the mined `OP_RETURN` pubkey matches the pubkey the user actually authorized off‑chain, rather than trusting whatever bytes appear on-chain.

### Proof of Concept
```rust
// cargo test in core/src/test (regtest-based, no mainnet/live Citrea)
// 1. Perform a normal withdrawal setup: create dust UTXO, sign with
//    generate_withdrawal_transaction_and_signature (SinglePlusAnyoneCanPay).
// 2. Build payout tx A via create_payout_txhandler with operator_xonly_pk = honest_op_pk,
//    fund it (extra input from a test wallet), broadcast+mine it -> assert
//    verifier.update_finalized_payouts attributes payout to honest_op_pk.
// 3. In a fresh chain state, instead build payout tx B reusing the SAME input/signature
//    and SAME output[0], but set operator_xonly_pk = victim_op_pk in the OP_RETURN,
//    fund with attacker-controlled extra input, broadcast+mine it.
// 4. Assert: SECP.verify_schnorr on the reused signature against tx B's sighash still
//    passes (binding "signature valid" holds), yet
//    db.get_payout_info_from_move_txid(...).0 == Some(victim_op_pk)
//    even though victim_op_pk never signed or funded tx B — violating
//    "operator xonly pk recorded == party whose funds paid the payout".
```

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L413-435)
```rust
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

**File:** core/src/verifier.rs (L2311-2342)
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
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
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
```
