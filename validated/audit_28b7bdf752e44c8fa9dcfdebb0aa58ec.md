## Title
Payout transaction's operator-attribution (OP_RETURN) is not covered by the user's withdrawal signature, allowing malleation of who is credited for fronting a peg-out - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` builds the payout transaction with a user-signed output at index 0, an anchor at index 1, and an OP_RETURN output at index 2 that encodes the operator's x-only pubkey used later to attribute reimbursement credit. [1](#0-0)  The user's signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which BIP341 defines to commit only to the input being spent and the **single output at the same index** as that input. [2](#0-1) [3](#0-2)  Since the signed input is index 0 and the signed output is therefore also index 0 (the user payout), the anchor and OP_RETURN outputs (indices 1 and 2) are excluded from the sighash and can be altered without invalidating the user's signature.

### Finding Description
The bridge's reimbursement bookkeeping (`payout_payer_operator_xonly_pk` in the `withdrawals` table) is derived purely from parsing the OP_RETURN output of the confirmed payout transaction: [4](#0-3)  This value is later used to decide which operator is allowed to claim reimbursement (`get_first_unhandled_payout_by_operator_xonly_pk`, `validate_payer_is_operator`) [5](#0-4) [6](#0-5) , and to decide whether a kickoff is "malicious" (`is_kickoff_malicious`) by comparing it against the OP_RETURN-derived pubkey. [7](#0-6) 

Because the OP_RETURN output is unsigned by the withdrawing user, and the transaction is still mutable at the point the operator's `withdraw()` flow funds it with `fund_raw_transaction` before final signing/broadcast [8](#0-7) , the binding "operator credited == the party that actually funded the payout" is not enforced by the on-chain signature scheme; it relies entirely on trusting that whichever operator constructs/broadcasts the transaction places their own pubkey in the OP_RETURN honestly, and that no other party can rewrite that field before confirmation.

I was **not able to fully verify** (due to running out of tool iterations) exactly how the additional funding inputs added by `fund_raw_transaction` are subsequently signed (e.g., via `signrawtransactionwithwallet`), and whether that signing step uses a default `SIGHASH_ALL` that would re-bind the full output set (including the OP_RETURN) once those additional inputs are added. If the final funding/signing step commits the entire transaction (all outputs) via `SIGHASH_ALL` on the added change/fee input, this would close the malleability window described above, and the finding would be reduced to a design remark. This uncertainty should be resolved in the source (`core/src/operator.rs`, past line 675, and the RPC signing path) before treating this as exploitable in production.

### Impact Explanation
If the OP_RETURN can be swapped by an unauthorized third party before the operator's payout transaction confirms (e.g., via mempool observation/replacement), the impact maps to the Critical class "an operator reimbursed for a payout it never funded" or "an honest operator permanently unable to be reimbursed" — the exact binding the VAIVault-style report targets (credited party vs. paying party). The withdrawing user is unaffected (their signed output is untouched), but the operator-reimbursement accounting, and downstream fraud-detection logic in `is_kickoff_malicious`, would misattribute the payout.

### Likelihood Explanation
Likelihood is uncertain without confirming the funding/signing step. If additional inputs are added by `fund_raw_transaction` but ultimately signed with `SIGHASH_ALL` (bitcoind's default), the whole output set becomes locked before broadcast, which would make this non-exploitable as described. This must be verified in the surrounding code before concluding exploitability, so I cannot assert a confirmed exploit path with full confidence.

### Recommendation
- Confirm (in `core/src/operator.rs` after line 675, and any code that signs/finalizes the funded PSBT/tx) whether all inputs — including the ones added by `fund_raw_transaction` — are signed with `SIGHASH_ALL` (or otherwise commit to every output) before broadcast.
- If not, use a sighash type that covers all outputs for at least one operator-controlled input (e.g. `SIGHASH_ALL`, not `ANYONECANPAY`) for the funding input, or bind the OP_RETURN attribution cryptographically (e.g., have the operator sign a commitment over the OP_RETURN content that is checked against the mined tx) so the credited operator cannot be altered post-signature by any third party.

### Proof of Concept
Not able to construct a concrete, verified PoC within available context — this requires confirming the exact signing sequence for `fund_raw_transaction`'s added inputs in `core/src/operator.rs` (past line 675) and the RPC wallet-signing call, which I could not retrieve before running out of tool budget. A background engineering session with full read access to `core/src/operator.rs` (complete `withdraw()` function through broadcast) is recommended to confirm whether the described malleability window is real, and if so, produce a concrete two-transaction PoC (original operator payout tx vs. attacker-crafted variant with swapped OP_RETURN, same signed input/output, differing anchor/OP_RETURN, both passing `testmempoolaccept`).

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

**File:** core/src/operator.rs (L1686-1729)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L850-862)
```rust
    // Data about this output:
    if sighash == TapSighashType::Single {
        let mut enc_single_output = sha256::Hash::engine();
        let output = tx
            .output
            .get(input_index)
            .expect("SIGHASH_SINGLE requires a corresponding output");
        output
            .consensus_encode(&mut enc_single_output)
            .expect(expect_msg);
        let hash = sha256::Hash::from_engine(enc_single_output);
        hash.consensus_encode(writer).expect(expect_msg);
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

**File:** core/src/database/verifier.rs (L282-296)
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
```
