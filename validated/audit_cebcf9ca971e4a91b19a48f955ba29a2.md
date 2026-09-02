### Title
Payout OP_RETURN operator attribution is unauthenticated malleable data, allowing permanent loss of operator reimbursement - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` signs the payout transaction's input 0 with `SinglePlusAnyoneCanPay`, which (per BIP-341/342 Taproot sighash rules) commits only to input 0 and output 0. Output 2, the OP_RETURN carrying the fronting operator's `operator_xonly_pk`, is completely uncommitted by the signature, so any third party observing the broadcast tx can construct a fee-bumped variant that keeps the valid signature/input0/output0 but swaps or removes the OP_RETURN, permanently misattributing (or erasing) the reimbursement credit that `Verifier::update_finalized_payouts` records.

### Finding Description
The binding the protocol relies on is:
`stored operator_xonly_pk for withdrawal i == xonly_pk of the party whose funds are used to front output 0 of the mined payout tx for i`.

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with input 0 = withdrawal UTXO (`SpendPath::KeySpend`), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(`operator_xonly_pk`). The operator signs/verifies only the user's signature over input 0 with `TapSighashType::SinglePlusAnyoneCanPay` (`core/src/operator.rs:630-637`). The manual sighash-writer in `circuits-lib/src/bridge_circuit/mod.rs:731-810` (and standard Taproot sighash semantics) shows that for `Single`, `sha_outputs` is *not* included, and `AnyoneCanPay` excludes commitments to other inputs — i.e. only output index 0 and the signer's own input are committed.

Because the operator's own `operator_xonly_pk` in output 2 is never covered by any signature, once the honest operator broadcasts the payout tx (via `fund_raw_transaction`, no `replaceable` flag pinned, so it inherits wallet RBF defaults — `core/src/operator.rs:651-673`), an attacker who observes the mempool tx can:
1. Copy input 0 (same outpoint, same valid witness signature) and output 0 (same payout, required since it's covered by SIGHASH_SINGLE).
2. Freely add their own fee-paying input(s) and rewrite outputs 1/2, e.g. replacing the OP_RETURN with a different xonly pubkey, or dropping it entirely.
3. Broadcast with higher fee (RBF) to get their variant mined instead of the honest operator's original.

`Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) then parses whichever payout tx actually gets mined: it calls `get_first_op_return_output`/`parse_op_return_data` (`circuits-lib/src/bridge_circuit/mod.rs:608-617, 686-692`) and stores whatever `operator_xonly_pk` (or `None`) is found for withdrawal `i` via `db.update_payout_txs_and_payer_operator_xonly_pk`. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-51`) only picks up unhandled payouts by exact match on `operator_xonly_pk` (`get_first_unhandled_payout_by_operator_xonly_pk`, `core/src/database/verifier.rs:282-313`). If the stored key is wrong or `NULL`, the honest operator's `handle_finalized_payout` → Reimburse path is never triggered for that withdrawal, even though they were the one who actually funded output 0.

No existing guard (`Verifier::is_deposit_valid`, `SECP.verify_schnorr`, mempool/RBF policy, or a DB uniqueness constraint) prevents this, because the user's signature intentionally only authorizes input 0/output 0 under `SinglePlusAnyoneCanPay` — this is by design to allow fee-bumping, but the protocol incorrectly also trusts an unauthenticated OP_RETURN in the same transaction as the source of truth for reimbursement attribution.

### Impact Explanation
The honest operator has already irrevocably paid the user (output 0 is fixed and spent), but is permanently unable to be reimbursed because the on-chain accounting/checker keys off the malleable OP_RETURN rather than off any signed commitment. This matches the Critical category "an honest operator permanently unable to be reimbursed." The attack is repeatable for every withdrawal an operator fronts, across all operators, as long as the attacker can observe the mempool and outbid on fees before confirmation.

### Likelihood Explanation
The attacker only needs: (1) visibility into the Bitcoin mempool (public), (2) enough BTC to add a higher-fee input, and (3) the payout tx to still be unconfirmed when they act (feasible any time before the first confirmation, and easier if the honest operator's fee-rate estimate is conservative). No verifier/operator/aggregator privilege is required. This is a generic and cheap griefing vector against any operator-fronted payout.

### Recommendation
Do not derive the reimbursing operator's identity from an unauthenticated OP_RETURN output that isn't covered by any signature. Either (a) have the operator sign a commitment to their own xonly pubkey as part of a script/covenant that ties output 2 cryptographically to input 0 spending authority (e.g., commit to it via the connector/kickoff output that only the operator can produce), or (b) attribute reimbursement based on the kickoff tx's own signed commitments rather than trusting the payout tx's OP_RETURN content, or (c) require the operator to also fully sign (SIGHASH_ALL) a distinct linking transaction/commitment that binds their `operator_xonly_pk` before releasing funds, so an attacker cannot rewrite the attribution while reusing a SIGHASH_SINGLE|ANYONECANPAY signature.

### Proof of Concept
`cargo test` plan (regtest, no mainnet/live Citrea, using `create_test_config_with_thread_name`/`create_regtest_rpc` as in `core/src/test/withdraw.rs`):
1. Set up a deposit and withdrawal as in `core/src/test/manual_reimbursement.rs::deposit_and_get_reimbursement`, obtaining `dust_utxo`, `payout_txout`, `sig` from `generate_withdrawal_transaction_and_signature`.
2. Call operator's `withdraw` RPC (honest operator O1) to build+broadcast the payout tx via `create_payout_txhandler`, capturing the mempool tx `T1` (input0=dust_utxo, output0=payout, output2=OP_RETURN(O1.xonly_pk)).
3. Before `T1` confirms, construct `T2` reusing `T1.input[0]` (same witness) and `T1.output[0]`, add an attacker-funded extra input, set output2 to OP_RETURN(attacker-chosen pubkey or omit it), and broadcast `T2` with a higher fee rate so it replaces `T1` in the mempool (assert via `test_mempool_accept`/`getmempoolentry` that `T2` replaces `T1`).
4. Mine blocks so `T2` confirms; run block sync so `Verifier::update_finalized_payouts` processes it.
5. Assert: `db.get_payout_info_from_move_txid(...)` for the withdrawal returns `operator_xonly_pk != Some(O1.xonly_pk)` (either `None` or the attacker's key).
6. Assert: `db.get_first_unhandled_payout_by_operator_xonly_pk(O1.xonly_pk)` returns `None` indefinitely, and `PayoutCheckerTask::run_once` for O1 returns `Ok(false)` forever, proving O1 can never reach `handle_finalized_payout`/Reimburse for this withdrawal despite having funded output 0. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L799-810)
```rust
    }

    if sighash != TapSighashType::None && sighash != TapSighashType::Single {
        // Manually compute sha_outputs
        let mut enc_outputs = sha256::Hash::engine();
        for txout in tx.output.iter() {
            txout.consensus_encode(&mut enc_outputs).expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_outputs)
            .consensus_encode(writer)
            .expect(expect_msg);
    }
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

**File:** core/src/task/payout_checker.rs (L39-51)
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
```
