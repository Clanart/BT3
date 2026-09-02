### Title
Anyone who obtains a fronted-withdrawal's presigned SIGHASH_SINGLE|ANYONECANPAY signature can forge the OP_RETURN payer attribution, crediting an arbitrary operator with a reimbursement it never funded - ([File: core/src/verifier.rs])

### Summary
The `update_finalized_payouts` routine determines *who* gets reimbursed for a withdrawal purely by reading an unauthenticated `OP_RETURN` output from whatever transaction happens to spend the withdrawal UTXO, with no cryptographic binding between "the operator whose key is embedded in OP_RETURN" and "the party that actually funded the payout."

### Finding Description
`create_payout_txhandler` builds the payout transaction with input 0 spent via the user's presigned key-spend witness under `SIGHASH_SINGLE|ANYONECANPAY`, and appends an unsigned/unauthenticated third output (`op_return_txout`) carrying the fronting operator's x-only public key: [1](#0-0) 

Because the witness commits only to the sighash `SINGLE|ANYONECANPAY`, it authenticates input 0 together with output 0 (the user's payout) but places **no constraint whatsoever** on the OP_RETURN output's contents, or on any other outputs/inputs added to the transaction. Consequently, once the user's signature for a given withdrawal is known (it is a value passed to the `Withdraw`/`InternalWithdraw` RPC and to `parse_withdrawal_sig_params`, and is required to be echoed back for Citrea's `verification_signature` check, meaning it circulates outside of a single trusted operator), anyone able to construct and broadcast a valid transaction spending that specific withdrawal UTXO can choose an arbitrary x-only public key for the OP_RETURN output. [2](#0-1) 

The verifier's block-sync logic then blindly trusts this attacker-controlled OP_RETURN value as the payer identity: [3](#0-2) 

This value is persisted as `payout_payer_operator_xonly_pk` and used later to look up "the first unhandled payout" for a given operator's own key: [4](#0-3) [5](#0-4) 

`validate_payer_is_operator` only checks that the stored `payer_xonly_pk` equals the operator's own key - it never verifies that the operator itself broadcast or funded the payout transaction: [6](#0-5) 

This mirrors the reported bug class: the security-relevant check ("is the party credited with fronting the withdrawal the same party who actually paid?") is skipped whenever the crediting data comes from an unauthenticated, attacker-influenceable channel (the OP_RETURN output), exactly as the original report describes skipping the price-oracle sanity check for collateral that was never explicitly entered by the borrower. Here, the binding that should hold is:
`payout_payer_operator_xonly_pk (as attributed by verifier) == operator who actually funded output 0 of the payout tx`
but nothing enforces this equality; the attacker can set the left side to any registered operator's public key while a completely different (or no) operator pays the real cost.

### Impact Explanation
If an attacker fronts a user's withdrawal themselves (paying output 0 out of pocket, as permitted by `ANYONECANPAY`) but embeds a *different*, legitimate operator's x-only public key in the OP_RETURN output, that operator's `PayoutCheckerTask` will pick up the payout as "its own", proceed through `handle_finalized_payout`, generate a kickoff, and ultimately be reimbursed via `create_reimburse_txhandler` out of the move-to-vault UTXO: [7](#0-6) 

The result is that vault BTC is paid out to an operator who never funded the withdrawal - i.e., "an operator reimbursed for a payout it never funded," which is explicitly listed as a Critical impact category. This also silently marks a withdrawal as handled by that operator even though the operator's own bookkeeping/state (kickoff commitments, watchtower data) may not correspond to a payout it actually initiated, potentially causing chained inconsistencies in the challenge/reimbursement state machine.

### Likelihood Explanation
Exploitation only requires: (1) knowledge of the user's `input_signature` for a specific withdrawal, which is passed through RPC parameters and echoed in the EIP-712 verification message rather than being kept operator-private, and (2) the ability to fund and broadcast a Bitcoin transaction spending the withdrawal UTXO with a custom OP_RETURN output. No verifier, operator, or aggregator role, and no key compromise, is required - the attacker only needs standard Bitcoin transaction construction capability and the (non-secret-by-design) SIGHASH_SINGLE|ANYONECANPAY user signature that circulates through the withdrawal flow. This satisfies the "unauthenticated state-changing/broadcasting call" bar for an unprivileged actor.

### Recommendation
Do not trust an OP_RETURN value observed on-chain as sufficient proof that the named operator funded the payout. Instead, require the payout transaction's payer attribution to be cryptographically bound to the operator that actually signs/broadcasts it - e.g., have the operator co-sign the payout transaction (or its OP_RETURN output) with their own key so that `SIGHASH` coverage extends to that output, and have the verifier validate that signature rather than merely parsing an unauthenticated OP_RETURN byte string.

### Proof of Concept
1. Obtain the presigned `input_signature` (SIGHASH_SINGLE|ANYONECANPAY) and withdrawal parameters (`input_outpoint`, `output_script_pubkey`, `output_amount`) for a pending Citrea withdrawal - this data flows through the `Withdraw`/`optimistic_payout_sign` RPC surface and EIP-712 verification message and is not confined to a single operator's control, per `core/src/rpc/operator.rs:168-258` and `core/src/rpc/ecdsa_verification_sig.rs:20-93`.
2. Construct a transaction that spends `input_outpoint` as input 0 with the known signature, sets output 0 to exactly `(output_script_pubkey, output_amount)` as required by the SINGLE sighash, and add an arbitrary OP_RETURN output at a later index containing the x-only public key of a target operator `X` (who never participated in constructing or broadcasting this transaction).
3. Broadcast and confirm this transaction. The verifier's `update_finalized_payouts` (`core/src/verifier.rs:2312-2342`) will parse the OP_RETURN, store `payout_payer_operator_xonly_pk = X`.
4. Operator `X`'s `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-79`) finds this as its "first unhandled payout" and proceeds to reimbursement, ultimately claiming the move-to-vault funds via `create_reimburse_txhandler`, despite never having funded the withdrawal itself.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-384)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
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

**File:** core/src/rpc/operator.rs (L168-190)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_withdraw(
        &self,
        request: Request<WithdrawParams>,
    ) -> Result<Response<RawSignedTx>, Status> {
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(request.into_inner())?;

        tracing::warn!("Called internal_withdraw with withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount);

        let payout_tx = self
            .operator
            .withdraw(
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
            )
            .await?;

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
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
