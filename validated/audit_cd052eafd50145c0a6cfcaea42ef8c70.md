Based on my analysis, this vulnerability is confirmed and valid.

### Title
Attribution of a payout to the operator who funded it is unauthenticated, letting anyone strip its OP_RETURN and permanently deny the operator's reimbursement - (File: core/src/verifier.rs)

### Summary
The `create_payout_txhandler` payout transaction is signed only by the user's withdrawal signature under `TapSighashType::SinglePlusAnyoneCanPay`, which commits to input 0 and output 0 only, leaving the anchor output and the OP_RETURN operator-attribution output (output 2) completely unsigned and free for anyone to rewrite as long as the same input/output-0 pair is reused. Since `Verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` purely from whatever OP_RETURN happens to be in the transaction that is first mined spending the withdrawal UTXO, an attacker who observes the honest operator's broadcast payout tx in the mempool can build a variant with a stripped/invalid OP_RETURN and race it into a block first, writing `NULL` attribution and permanently blocking `PayoutCheckerTask::run_once`'s `get_first_unhandled_payout_by_operator_xonly_pk` lookup for the honest operator.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` (as set by `Verifier::update_finalized_payouts`) should equal the x-only public key of the operator whose funds actually paid output 0 of the withdrawal (i.e., the operator that ran `Operator::withdraw`/broadcast the confirmed payout tx).

Code path:
1. `Operator::withdraw` (`core/src/operator.rs:560-637`) builds the payout tx via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`). The tx has three outputs: user payout (0), anchor (1), OP_RETURN with `operator_xonly_pk` (2). Only input 0 is signed, using the *user's* signature and sighash type [1](#0-0) .
2. The sighash type for this user signature is enforced to be `TapSighashType::SinglePlusAnyoneCanPay` at the RPC parsing layer [2](#0-1) . `SIGHASH_SINGLE|ANYONECANPAY` only commits the signature to the single input being spent and the output at the same index (output 0); outputs 1 (anchor) and 2 (OP_RETURN) are not covered by any signature at all [3](#0-2) .
3. Once the operator broadcasts this transaction, its witness (containing the valid user signature) is publicly visible in the mempool. Any unprivileged party can copy input 0 + output 0, and construct a new transaction spending the same withdrawal UTXO with an arbitrary, unsigned output 1/2 — including no OP_RETURN or a malformed one — and get it mined with a higher fee before the honest tx confirms.
4. `Verifier::update_finalized_payouts` picks up whichever transaction actually spent the tracked `withdrawal_utxo_txid`/`withdrawal_utxo_vout` in a newly synced block, via `get_payout_txs_for_withdrawal_utxos` which joins on the UTXO, not any specific txid [4](#0-3) . It then parses the OP_RETURN and sets `operator_xonly_pk` to `None` if absent or invalid [5](#0-4) , persisting `NULL` via `update_payout_txs_and_payer_operator_xonly_pk` [6](#0-5) .
5. `PayoutCheckerTask::run_once` is the only automated trigger for `Operator::handle_finalized_payout`, and it queries strictly by exact equality on `payout_payer_operator_xonly_pk = $1` [7](#0-6) ; a `NULL` row never matches, so the honest operator's task loop never observes this withdrawal as unhandled [8](#0-7) . The only other trigger, `internal_finalized_payout`, is gated to test builds only [9](#0-8) .

Because the withdrawal UTXO can only be spent once, once the attacker's malformed tx is mined the honest operator's identical-input tx can never confirm afterward, so the attribution loss is effectively permanent barring a chain reorg.

### Impact Explanation
The user is still paid correctly (output 0 unchanged), but the operator who fronted the withdrawal is permanently denied its `Reimburse` path, since `is_kickoff_malicious` and `send_asserts` both require a non-`None` `payout_payer_operator_xonly_pk` matching the kickoff operator before allowing reimbursement flow to proceed [10](#0-9) [11](#0-10) . This matches the Critical category "an honest operator permanently unable to be reimbursed." It is repeatable against any operator/withdrawal pair whose payout tx is observable pre-confirmation, at the cost of one competing Bitcoin transaction with a higher fee.

### Likelihood Explanation
Requires only: a registered Citrea withdrawal, an operator's honestly broadcast (but unconfirmed) payout tx visible in the Bitcoin mempool, and the attacker paying a higher fee to get their variant mined first — all reachable by an unprivileged Bitcoin-network participant with no special protocol access. This is a low-cost, deterministic race, not a probabilistic or resource-exhaustion attack; it directly exploits the fact that `SinglePlusAnyoneCanPay` leaves the OP_RETURN output unauthenticated.

### Recommendation
Bind operator attribution cryptographically to the payout transaction rather than trusting an unauthenticated OP_RETURN output. Options: have the operator co-sign the payout transaction (e.g., adding an operator-signed input or committing to the OP_RETURN via a signature covering all outputs), or otherwise anchor the attribution to something the attacker cannot rewrite without invalidating the user's signature — for instance requiring the OP_RETURN to be covered by a `SIGHASH_ALL`-style commitment, or deriving attribution from off-chain operator records cross-checked against on-chain data instead of solely from the confirmed OP_RETURN.

### Proof of Concept
```
#[tokio::test]
async fn test_op_return_stripping_denies_honest_operator_reimbursement() {
    // 1. Set up regtest bridge, register withdrawal `i` on (mocked) Citrea for honest operator's xonly_pk.
    // 2. Have honest operator broadcast its correctly-formed payout tx (input0 = withdrawal_utxo,
    //    output0 = user payout, output2 = OP_RETURN(honest_op_xonly_pk)) but DO NOT mine it.
    // 3. Extract input0 + witness (user sig) + output0 from the mempool tx; build a new tx spending
    //    the same withdrawal_utxo with output1=anchor, output2=stripped/invalid OP_RETURN, and a
    //    higher fee (extra funding input added by attacker).
    // 4. Broadcast attacker's tx, mine it to confirmation (simulating bitcoin_syncer block processing
    //    that triggers Verifier::update_finalized_payouts).
    // 5. Assert: db.get_payout_info_from_move_txid(...).0 == None  (payer_xonly_pk is NULL)
    // 6. Assert: db.get_first_unhandled_payout_by_operator_xonly_pk(honest_op_xonly_pk) returns None
    //    even after polling for an extended period / additional blocks, proving the honest operator's
    //    PayoutCheckerTask::run_once never observes this withdrawal as unhandled.
}
```

### Citations

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

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
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

**File:** core/src/database/verifier.rs (L198-251)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;

        Ok(())
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

**File:** core/src/rpc/operator.rs (L373-382)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR), ret(level = tracing::Level::TRACE))]
    async fn internal_finalized_payout(
        &self,
        request: Request<FinalizedPayoutParams>,
    ) -> Result<Response<clementine::Txid>, Status> {
        if !cfg!(test) {
            return Err(Status::permission_denied(
                "This method is only available in tests",
            ));
        }
```
