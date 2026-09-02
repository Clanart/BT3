This confirms the mechanism: `get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) matches purely by which transaction spent `withdrawal_utxo_txid`/`withdrawal_utxo_vout` in `bitcoin_syncer_spent_utxos` — it does not check who funded the transaction or require the OP_RETURN to be present/valid at that stage. The OP_RETURN parsing only happens afterward, in `update_finalized_payouts` (core/src/verifier.rs:2283-2352), to attribute a payer. [1](#0-0) [2](#0-1) 

The critical enabling fact is the payout transaction's signature scheme: `create_payout_txhandler` builds a KeySpend-only input (the withdrawal UTXO, owned and signed solely by the withdrawer with `SinglePlusAnyoneCanPay`), so the OP_RETURN output and any funding inputs are **not covered by the user's signature** and can be freely substituted by whoever assembles the final transaction. [3](#0-2) [4](#0-3) 

Since the withdrawer generates this `SinglePlusAnyoneCanPay` signature themselves before ever handing it to an operator, they can independently build their own variant of the payout transaction — same input/output-0, but self-funded and with an invalid/missing OP_RETURN — and broadcast it directly to Bitcoin, racing the operator's fronting transaction. [5](#0-4) 

Once that attacker-controlled variant confirms, `update_finalized_payouts` stores `payout_payer_operator_xonly_pk = NULL` for that withdrawal row, exactly per the code's own comment. [6](#0-5) 

`get_first_unhandled_payout_by_operator_xonly_pk` filters strictly on `w.payout_payer_operator_xonly_pk = $1`, which can never equality-match against NULL for any operator's xonly pk. [7](#0-6) 

`PayoutCheckerTask::run_once` is the only production trigger for `handle_finalized_payout`/kickoff/Reimburse (the equivalent test-only RPC `internal_finalized_payout` is gated behind `cfg!(test)`), so with a NULL payer this withdrawal's deposit is never routed through it by any operator. [8](#0-7) [9](#0-8) 

As a further backstop, `Verifier::is_kickoff_malicious` treats a missing `operator_xonly_pk` in the payout info as automatically malicious, so even an out-of-band manual kickoff attempt for this deposit would be flagged and challengeable, closing off any alternate reimbursement path. [10](#0-9) 

This is distinct from `create_optimistic_payout_txhandler`, which directly spends the `MoveToVault`/`DepositInMove` output via N-of-N verifier signature and thus resolves the deposit without needing operator attribution at all — that is the legitimate "optimistic payout" case the code comment refers to, and it is not what's exploited here. [11](#0-10) 

### Title
Withdrawer can self-broadcast the regular payout tx with an invalid/omitted OP_RETURN, permanently freezing the deposit's move-to-vault UTXO - (File: core/src/verifier.rs, core/src/database/verifier.rs, core/src/task/payout_checker.rs)

### Summary
The regular (non-optimistic) payout transaction's user signature only covers the withdrawal input and the user's own output (`SinglePlusAnyoneCanPay`, `SIGHASH_SINGLE`), leaving the OP_RETURN output and additional funding inputs unsigned. A withdrawer, who already possesses this signature (they created it themselves), can self-fund and broadcast their own version of the payout transaction with a corrupted or missing OP_RETURN before any operator's version confirms, causing `payout_payer_operator_xonly_pk` to be recorded as NULL. Because `get_first_unhandled_payout_by_operator_xonly_pk` only matches non-NULL, operator-scoped values, this withdrawal is permanently excluded from `PayoutCheckerTask`, and `is_kickoff_malicious` blocks any out-of-band kickoff attempt, permanently freezing the deposit's move-to-vault BTC.

### Finding Description
Binding: for every finalized withdrawal whose payout output has confirmed on Bitcoin, there must exist a value `operator_xonly_pk` such that `get_first_unhandled_payout_by_operator_xonly_pk(operator_xonly_pk)` eventually returns that withdrawal for the operator that actually funded it, i.e. `payout_payer_operator_xonly_pk == funding_operator.xonly_pk`. This is broken when `payout_payer_operator_xonly_pk == NULL`, which can never equal any operator's xonly pk in the SQL `WHERE payout_payer_operator_xonly_pk = $1` filter (`core/src/database/verifier.rs:282-296`).

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) constructs the payout transaction with a single KeySpend input (the user's withdrawal UTXO) signed with `SinglePlusAnyoneCanPay`/`SIGHASH_SINGLE`. This sighash type covers only input index 0 and output index 0 — the anchor output and the OP_RETURN output (carrying the fronting operator's xonly pubkey) are entirely unsigned and can be freely substituted by anyone assembling the final broadcastable transaction, along with any additional funding inputs (`ANYONECANPAY`).

The withdrawer creates this exact signature themselves before ever sending it to an operator (`core/src/rpc/parser/operator.rs:161-187` enforces the sighash type but the signature itself originates from the user). Thus the withdrawer can independently build their own copy of the payout transaction, self-funding the payout output and omitting or corrupting the OP_RETURN, then broadcast it directly, racing to have it mined ahead of any operator's fronting transaction.

The bitcoin syncer attributes "the payout tx" for a withdrawal purely by which transaction spends `withdrawal_utxo_txid`/`withdrawal_utxo_vout` (`get_payout_txs_for_withdrawal_utxos`, `core/src/database/verifier.rs:170-196`) — it does not validate who funded it. `update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) then tries to parse an operator xonly pk from the OP_RETURN; on failure it stores NULL, exactly matching the code's own comment that this occurs for "optimistic payout... or the operator constructed the payout tx wrong" — but a malicious self-broadcast by the withdrawer is a third, unaccounted-for cause.

Once NULL is stored, no operator's `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-47`) — the only production-path trigger of `Operator::handle_finalized_payout`/kickoff — will ever match this row. The test-only `internal_finalized_payout` RPC that could otherwise directly invoke `handle_finalized_payout` is disabled outside test builds (`core/src/rpc/operator.rs:378-382`). Even a manually-constructed kickoff for this deposit would be rejected as malicious by `Verifier::is_kickoff_malicious`, since it treats a missing OP_RETURN operator pk as automatically malicious (`core/src/verifier.rs:1882-1885`), so verifiers would never co-sign/accept the corresponding Reimburse flow.

### Impact Explanation
The move-to-vault UTXO for the affected deposit (holding `bridge_amount`, e.g. 1 BTC) becomes permanently frozen: no operator can ever be routed to kickoff/Reimburse for that deposit, and no honest operator that raced to front the withdrawal (but lost the race, since their unconfirmed alternative tx is now double-spent) retains a reachable reimbursement path either. This matches the Critical category "a move-to-vault UTXO permanently frozen." The attack is repeatable per deposit/withdrawal that goes through the regular (non-optimistic) payout path, and costs the attacker only Bitcoin transaction fees since the self-funded payout output returns to their own address.

### Likelihood Explanation
Preconditions are minimal and match the stated unprivileged attacker capabilities: the attacker only needs to be the withdrawer who calls `withdraw()` on the Citrea bridge contract and holds the withdrawal UTXO's private key (which they always do, since they generate the `SinglePlusAnyoneCanPay` signature themselves). No verifier, operator, or aggregator collusion is required. The attacker simply needs their self-funded, OP_RETURN-corrupted transaction to be the one that confirms spending the withdrawal UTXO, which they fully control the construction and broadcast timing of.

### Recommendation
Do not rely solely on OP_RETURN content to attribute payer identity for reimbursement eligibility. Instead, either (a) commit to the fronting operator's identity within the user-signed sighash (e.g., have the operator co-sign or require a script path bound to the operator xonly pk) so a substituted OP_RETURN invalidates the transaction rather than merely leaving payer NULL, or (b) add a recovery mechanism so that a NULL-attributed but genuinely valid/finalized payout can still route to `PayoutCheckerTask` for some operator (e.g., via manual/aggregator-driven attribution with proof-of-funding), while also hardening `is_kickoff_malicious` to accept a proven-alternate attribution path.

### Proof of Concept
In `core/src/task/payout_checker.rs` test module:
1. Set up a `Database` and insert a `withdrawals` row via `upsert_move_to_vault_txid_from_citrea_deposit` + `update_withdrawal_utxo_from_citrea_withdrawal` for a synthetic deposit.
2. Call `update_payout_txs_and_payer_operator_xonly_pk` with `operator_xonly_pk = None` for that index (simulating the attacker's self-broadcast payout with invalid/omitted OP_RETURN), as already exercised in `core/src/database/verifier.rs` test `update_get_payout_txs_from_citrea_withdrawal` (lines 496-513).
3. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(None, operator_xonly_pk).await.unwrap().is_none()` for every operator xonly pk in the test set (not just one) — showing no operator can ever be scheduled.
4. Run a `PayoutCheckerTask` for each such operator and assert `run_once()` returns `Ok(false)` indefinitely, confirming `handle_finalized_payout`/kickoff/Reimburse is never produced for this deposit.

### Citations

**File:** core/src/database/verifier.rs (L170-185)
```rust
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
```

**File:** core/src/database/verifier.rs (L286-296)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L459-491)
```rust
pub fn create_optimistic_payout_txhandler(
    deposit_data: &mut DepositData,
    input_utxo: UTXO,
    output_txout: TxOut,
    user_sig: taproot::Signature,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    let move_txhandler: TxHandler = create_move_to_vault_txhandler(deposit_data, paramset)?;
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::NotStored,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::non_ephemeral_anchor_output(),
        ))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    Ok(txhandler)
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

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

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

**File:** core/src/task/payout_checker.rs (L39-47)
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
```

**File:** core/src/rpc/operator.rs (L378-382)
```rust
        if !cfg!(test) {
            return Err(Status::permission_denied(
                "This method is only available in tests",
            ));
        }
```
