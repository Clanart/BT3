## Confirmed vulnerability

This claim is supported by the code and is a real, Critical-severity finding.

### The binding

Claimed binding: `recorded operator xonly pk for withdrawal i` (as read back by `Verifier::is_kickoff_malicious`) `== xonly pk of the operator who actually funded output0 of the confirmed payout tx`.

Trace confirms the binding is **not enforced** — it collapses to "whichever transaction happens to spend the withdrawal UTXO on-chain, regardless of its origin or outputs."

### Root cause / path

1. `Operator::withdraw` builds `create_payout_txhandler` and requires the user's signature to verify under `SinglePlusAnyoneCanPay` sighash (comment at core/src/operator.rs:637 explicitly documents this expected flag), meaning the signature only commits to input0 and output0 — any other input/output (fee input, change, anchor, OP_RETURN) is unauthenticated and freely re-arrangeable by anyone who can construct a valid witness for input0. [1](#0-0) [2](#0-1) 

2. The database looks up the payout tx for a withdrawal purely by which txid spent the recorded `withdrawal_utxo_txid`/`vout` on-chain — there is no check that the confirmed tx matches the operator's originally broadcast one, its outputs, or that it contains an OP_RETURN: [3](#0-2) 

3. `update_finalized_payouts` reads this arbitrary confirmed tx, tries to extract the OP_RETURN operator pubkey, and sets it to `None` if absent: [4](#0-3) 

4. `is_kickoff_malicious` then treats `operator_xonly_pk_opt == None` as an unconditional "assume malicious" branch, before ever checking the committed payout blockhash: [5](#0-4) 

5. `handle_kickoff` uses this result to queue a `Challenge` tx against the honest operator's kickoff on all non-mainnet/testnet4 networks: [6](#0-5) 

### Why existing guards don't catch this
- `SECP.verify_schnorr` only validates the S+AP signature over input0/output0 — it explicitly does not, and cannot, bind the OP_RETURN output or the anchor output.
- `get_payout_txs_for_withdrawal_utxos` performs a pure outpoint-spend lookup with no txid/output equality check to the operator's originally signed transaction.
- `is_kickoff_malicious` treats "no operator pubkey found" (which conflates "optimistic payout" with "attacker-stripped OP_RETURN") as malicious unconditionally, never falling through to compare the committed blockhash for this specific class of missing-pubkey case.

This matches the "None-fallback" comment in the code itself acknowledging that `operator_xonly_pk` can legitimately be `None` for optimistic payouts, showing the code author was aware of the ambiguity but did not distinguish attacker-induced `None` from legitimate optimistic-payout `None`.

### Impact

An unprivileged attacker who observes operator A's unconfirmed payout tx in the mempool can rebuild a variant using the same signed input0 (valid under S+AP) and output0, drop the OP_RETURN and any other operator-added outputs, add only an anchor, raise the fee, and get it mined instead. Once mined, verifiers record `operator_xonly_pk = None` for that withdrawal and will vote `is_kickoff_malicious = true` for operator A's legitimate kickoff, causing a Challenge to be sent and burning A's collateral — an honest operator's collateral is destroyed despite ostensibly correct behavior. This is repeatable for any withdrawal/operator using this signature scheme, at the cost only of one transaction's fee bump.

### Recommendation
- Don't let `operator_xonly_pk_opt == None` short-circuit to "malicious" — fall through and still validate the committed payout blockhash matches the kickoff witness data; only flag malicious if the blockhash mismatches or nothing was committed correctly, distinguishing attacker-stripped payouts from genuine optimistic payouts.
- Alternatively (better), avoid `SIGHASH_SINGLE|ANYONECANPAY` for the OP_RETURN-critical field, or explicitly commit the operator pubkey inside the signed portion of the payout tx (e.g. via `SIGHASH_ALL` or embedding pubkey commitment in output0's script) so it cannot be stripped by a third party while keeping output0 intact.
- Track payout confirmation by full-tx match (txid known when operator broadcast) rather than purely by outpoint-spend, rejecting/ignoring unknown replacement transactions for reconciliation purposes and treating them as separate evidence requiring operator-specific handling.

### Proof of Concept sketch
`cargo test` (mock_citrea style, regtest, no mainnet):
1. Fund a withdrawal, call `Operator::withdraw` for operator A, capture the broadcast payout tx (input0, output0=user payout, output1=anchor, output2=OP_RETURN(A's xonly pk)).
2. Before confirmation, build an attacker tx reusing input0's witness (valid because S+AP) and output0, but only two outputs (payout + anchor), with a higher fee, and broadcast/replace in mempool.
3. Mine it.
4. Run the verifier's `update_finalized_payouts` / `handle_finalized_block` flow, then assert `db.get_payout_info_from_move_txid` returns `operator_xonly_pk_opt == None`.
5. Call `handle_kickoff` for operator A's subsequent legitimate kickoff (with correct committed payout blockhash) and assert it returns `is_malicious == true` and a `Challenge` tx is queued to `tx_sender`, despite A's committed blockhash being fully correct.

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

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
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

**File:** core/src/verifier.rs (L2311-2328)
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
```
