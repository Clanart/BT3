## Title
Attacker-controlled OP_RETURN in a competing SIGHASH_SINGLE|ANYONECANPAY spend of a withdrawal UTXO lets an unprivileged party attribute a payout to an arbitrary registered operator, causing an unfunding operator to be automatically reimbursed - (File: `core/src/verifier.rs`, `core/src/task/payout_checker.rs`)

### Summary
`is_kickoff_malicious` and the fully automated `PayoutCheckerTask` credit whichever registered operator's x-only pubkey happens to appear in the OP_RETURN of the on-chain transaction that spends the withdrawal UTXO, with no cryptographic binding between that pubkey and the party who actually funded the payout output. Since the withdrawal input is signed with `SinglePlusAnyoneCanPay`, that signature does not commit to the OP_RETURN output or to which party supplies the extra funding input, so anyone possessing the user's withdrawal signature (obtainable via the public `withdraw` gRPC call broadcast to all operators) can construct and win-race their own funded spend naming any currently-registered operator, breaking attribution.

### Finding Description
The broken binding: `operator_xonly_pk` (recorded in `withdrawals.payout_payer_operator_xonly_pk`, parsed only from the *first OP_RETURN output* of whichever transaction happens to spend `withdrawal_utxo_txid:withdrawal_utxo_vout`) is claimed to equal "the operator who fronted the withdrawal with its own funds." In reality this equality is never enforced.

Code path:
1. `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) takes whatever transaction spent the withdrawal outpoint (from `bitcoin_syncer_spent_utxos`, i.e. the one Bitcoin transaction that actually confirmed) and blindly parses `operator_xonly_pk` from its first OP_RETURN output via `parse_op_return_data` / `XOnlyPublicKey::from_slice` [1](#0-0) , then persists it with `update_payout_txs_and_payer_operator_xonly_pk` [2](#0-1) .
2. `is_kickoff_malicious` reads this value back via `get_payout_info_from_move_txid` and treats a match against `kickoff_data.operator_xonly_pk` as proof of non-maliciousness [3](#0-2) .
3. `PayoutCheckerTask::run_once` fully automatically watches for `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` and, if a row is found, calls `handle_finalized_payout` to build and queue a kickoff/reimbursement, with no additional check that the operator actually broadcast/funded that specific payout tx [4](#0-3) .
4. The withdrawal input signature is created and verified under `TapSighashType::SinglePlusAnyoneCanPay` (`parse_withdrawal_sig_params` enforces this sighash type) [5](#0-4) . This sighash flag commits only to the single input and the output at the same index; it does **not** cover any other inputs (e.g., the funding input added by whoever constructs the transaction) or the OP_RETURN output, which is appended by `create_payout_txhandler` after the user's signed output [6](#0-5) .
5. The aggregator's public `withdraw` RPC broadcasts the withdrawal parameters (including the user's signature) to *all* currently registered operators without restricting who may act on them [7](#0-6) , and nothing prevents a non-operator attacker from independently invoking the equivalent Bitcoin RPC construction: take the same signed dust withdrawal input + same user output, add their own funding input to cover the output amount and fee, and append an OP_RETURN naming operator B (any currently-registered operator, taken from the aggregator's public operator-key list). Because only one spend of the withdrawal outpoint can ever confirm, whichever transaction wins the race (attacker's, broadcast with a competitive fee) becomes "the" payout tx recorded in `bitcoin_syncer_spent_utxos`, and its OP_RETURN is what `update_finalized_payouts` parses.
6. Once this attacker tx confirms, verifier DBs (all verifiers process the same chain) record `payout_payer_operator_xonly_pk = B`. Operator B's own `PayoutCheckerTask` polling loop will discover this "unhandled payout ... FOR operator_xonly_pk = B" row and automatically start the kickoff/reimbursement flow — B never signed off on being the payer, never funded anything, yet gets reimbursed by the N-of-N-signed presigned transaction graph, because `is_kickoff_malicious` only compares the untrusted OP_RETURN pubkey against `kickoff_data.operator_xonly_pk`, which will match since B's own kickoff naturally carries B's own pubkey.

No existing guard closes this: `is_kickoff_malicious` only checks OP_RETURN==kickoff operator and a blockhash commitment, neither of which authenticates who funded the payout; `validate_payer_is_operator` (`core/src/operator.rs:1687-1739`) only re-checks the DB's `payout_payer_operator_xonly_pk` against `self.signer.xonly_public_key`, which is exactly the value the attacker forged; `SECP.verify_schnorr` on the withdrawal signature only authenticates the withdrawing user's intent for their own output, not who pays it or what OP_RETURN accompanies it.

### Impact Explanation
Operator B is credited with a reimbursement (BTC from the presigned round/kickoff/reimburse transaction chain) for a withdrawal it never funded — Critical impact: "an operator reimbursed for a payout it never funded." Simultaneously, once `withdrawals.payout_txid`/`payout_tx_blockhash` are set for that withdrawal index, real operator A (who may have separately fronted or intended to front the same withdrawal) can never register as payer for the same deposit — matching the Critical category "an honest operator permanently unable to be reimbursed." The attacker's cost is bounded by the withdrawal amount (they must actually pay the user in real BTC to win the race and construct a valid competing spend) plus a fee premium to out-race the intended operator, but the attacker's loss is largely offset because they redirect the reimbursement entitlement to any registered operator of their choosing — repeatable per withdrawal/operator pair, with blast radius scaling to every future withdrawal in the system as long as `is_kickoff_malicious`/`update_finalized_payouts` remain unchanged.

### Likelihood Explanation
Preconditions are attacker-feasible: withdrawal parameters and signatures are visible in `withdraw` gRPC requests broadcast to *all* operators (no operator-restriction enforced against non-operators, and per the request flow this is functionally public data an unprivileged party can obtain by simply calling `withdraw` themselves or observing operator behavior/mempool). Constructing the competing transaction requires only standard Bitcoin tx-building knowledge (SIGHASH_SINGLE|ANYONECANPAY reuse is a well-known technique) and paying the withdrawal amount plus a winning fee. No verifier, operator, or aggregator privilege is needed. This is repeatable for every withdrawal processed by the bridge, though it requires the attacker to race and confirm before the legitimate operator's payout, which is a timing/fee-bidding contest an attacker with sufficient capital can reliably win.

### Recommendation
Do not trust the OP_RETURN pubkey alone as attribution. Require a proof that the named operator actually authorized/funded the specific payout, e.g., by requiring an operator signature over the payout transaction (or over `move_txid || withdrawal_idx || output`) that is checked in `update_finalized_payouts`/`is_kickoff_malicious`, or by requiring the payout's funding input(s) to be provably controlled by the claimed operator's registered collateral/wallet (e.g., an operator-specific covenant/prevout ownership check), instead of parsing an arbitrary unauthenticated OP_RETURN field.

### Proof of Concept
`cargo test` plan (place in `core/src/test`, though the underlying vulnerability is in `core/src/verifier.rs`/`core/src/database/verifier.rs`, not the test file itself):
1. Set up a deposit and withdrawal as in `deposit_and_withdraw_e2e.rs`, with two registered operators A and B.
2. Obtain the user's `SinglePlusAnyoneCanPay` withdrawal signature (as available to any `withdraw` gRPC caller).
3. Instead of letting operator A's `create_payout_txhandler`+`fund_raw_transaction` flow broadcast, independently build a payout transaction reusing the same signed dust input and same user output, add a self-funded input covering the output amount/fee, and set the OP_RETURN to operator B's xonly_pk. Broadcast with higher fee and mine it first.
4. Wait for `update_finalized_payouts` to run and assert `db.get_payout_info_from_move_txid(None, move_txid).0 == Some(B_xonly_pk)`.
5. Have operator B build/send its own kickoff (`KickoffData{operator_xonly_pk: B, ..}`) for this deposit and assert `is_kickoff_malicious(...)` returns `false`, and that `PayoutCheckerTask` for B marks the payout handled and queues a Reimburse tx — demonstrating B is reimbursed for funds it never provided, while A can no longer register as payer for the same withdrawal index.

### Citations

**File:** core/src/verifier.rs (L1871-1890)
```rust
        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
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

**File:** core/src/verifier.rs (L2312-2321)
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

**File:** core/src/task/payout_checker.rs (L39-79)
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
