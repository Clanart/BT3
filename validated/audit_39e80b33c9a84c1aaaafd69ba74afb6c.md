This confirms the mechanism: `PayoutCheckerTask::run_once()` in `core/src/task/payout_checker.rs` polls `get_first_unhandled_payout_by_operator_xonly_pk` filtered solely by `self.operator.signer.xonly_public_key`, and if a match is found, unconditionally calls `handle_finalized_payout()` which allocates a kickoff connector and proceeds toward the `Reimburse` transaction — crediting that operator for a withdrawal it never actually decided to front, verified, or checked for profitability via its own `withdraw()` RPC path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unauthenticated OP_RETURN operator pubkey lets anyone assign payout credit to an operator that never funded it - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs, core/src/task/payout_checker.rs)

### Summary
`create_payout_txhandler` embeds an arbitrary `operator_xonly_pk` into a plain `OP_RETURN` output with no signature or proof binding that key to whoever actually funds/broadcasts the payout transaction. `update_finalized_payouts` in the verifier blindly parses this `OP_RETURN` from any transaction that spends the registered withdrawal UTXO and stores it as `payout_payer_operator_xonly_pk` in the database, without verifying that the named operator authorized or paid for the payout. `PayoutCheckerTask` (and the operator's own `withdraw` RPC path) then unconditionally treats that DB record as proof of funding and drives the operator toward `Reimburse`, paying out bridge value.

### Finding Description
The payout transaction only requires the withdrawing user's own signature on their small dust input (`SpendPath::KeySpend` using `user_sig`), and any party constructing the payout tx can freely choose the `operator_xonly_pk` value baked into the `OP_RETURN` output: [5](#0-4) 

Since the withdrawing user legitimately possesses `in_signature` for their own withdrawal (this is their own signature over their own UTXO, per `WithdrawParams`), nothing prevents that same user from constructing and broadcasting this exact payout transaction directly to the Bitcoin network themselves — funding their own withdrawal — while embedding a chosen, unrelated, legitimate operator's `xonly_pk` in the `OP_RETURN`, entirely bypassing the operator's `withdraw()` RPC (and its `is_profitable` and verification-signature checks) since the RPC is never called.

The verifier's block scanner does not check any cryptographic tie between the named pubkey and the actual payer; it only parses the `OP_RETURN` bytes as an xonly public key: [6](#0-5) 

This value is persisted verbatim as `payout_payer_operator_xonly_pk`: [2](#0-1) 

The named operator's own automated `PayoutCheckerTask` then polls for unhandled payouts matching its own key and, upon finding one, unconditionally proceeds to `handle_finalized_payout`, allocating a kickoff connector and progressing toward the `Reimburse` transaction that pays the bridge_amount from the vault to that operator's reimbursement address: [7](#0-6) [8](#0-7) 

This breaks the intended binding: `payout_payer_operator_xonly_pk == the operator that actually fronted the withdrawal`. The attacker's forged `OP_RETURN` makes the equality false — a named operator is credited as payer while it never made the funding decision, never validated the withdrawal via `withdraw()`'s profitability/verification checks, and never signed anything asserting it is the payer.

### Impact Explanation
This matches the Critical impact class "an operator reimbursed for a payout it never funded." The named operator's kickoff/reimburse flow drains `bridge_amount` from the move-to-vault UTXO to that operator's reimbursement address for a withdrawal it did not choose to front, did not check for profitability, and did not authorize via any signature. This is bridge value leaving custody without that operator having provided a matching, self-authorized front.

### Likelihood Explanation
The withdrawing user always legitimately possesses the exact signature needed (`in_signature`) for their own withdrawal UTXO, since it is their own signature over their own dust input. Constructing and broadcasting the equivalent payout transaction with an arbitrary `OP_RETURN` requires no privileged access, no operator/verifier role, and no key compromise — only knowledge of the (public) `create_payout_txhandler` transaction format and the target operator's known public xonly key.

### Recommendation
Do not treat the `OP_RETURN` operator pubkey as authoritative proof of who funded the payout. Require operators to independently confirm and cryptographically commit (e.g., via the `withdraw()` RPC flow signature, or an operator signature over the payout details) before entering the kickoff/reimburse flow, or bind the `OP_RETURN` claim to an operator signature that can be verified on-chain/off-chain before `PayoutCheckerTask`/`handle_finalized_payout` acts on it.

### Proof of Concept
1. User registers/initiates a withdrawal on Citrea and obtains their own `in_signature` for the registered dust `withdrawal_utxo` (this is the user's own key, not secret from them).
2. Instead of calling any operator's `withdraw()` RPC, the user builds a payout transaction identical in shape to `create_payout_txhandler` (spend `withdrawal_utxo` with `in_signature`, pay `output_txout` to themselves or wherever, add the anchor output, and add an `OP_RETURN` containing a legitimate, uninvolved operator's `xonly_pk`).
3. User broadcasts this transaction directly to the Bitcoin network.
4. Once confirmed, `update_finalized_payouts` (`core/src/verifier.rs`) parses the forged `OP_RETURN` and records that operator as `payout_payer_operator_xonly_pk`.
5. That operator's `PayoutCheckerTask` (`core/src/task/payout_checker.rs`) picks up the "unhandled payout" for its own key and proceeds to `handle_finalized_payout`, eventually broadcasting a `Reimburse` transaction that pays `bridge_amount` from the vault to that operator's reimbursement address — even though it never funded, validated, or agreed to front this withdrawal.

### Citations

**File:** core/src/task/payout_checker.rs (L39-80)
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

**File:** core/src/verifier.rs (L2298-2343)
```rust
        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-385)
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
}
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L387-436)
```rust
/// Creates a [`TxHandler`] for the `payout_tx`.
///
/// This transaction is sent by the operator to front a peg-out, after which operator will send a kickoff transaction to get reimbursed.
///
/// # Inputs
/// 1. UTXO: User's withdrawal input (committed in Citrea side, with the signature given to operators off-chain)
///
/// # Outputs
/// 1. User payout output
/// 2. OP_RETURN output (with operators x-only pubkey that fronts the peg-out)
///
/// # Arguments
/// * `input_utxo` - The input UTXO for the payout, committed in Citrea side, with the signature given to operators off-chain.
/// * `output_txout` - The output TxOut for the user payout.
/// * `operator_xonly_pk` - The operator's x-only public key that fronts the peg-out.
/// * `user_sig` - The user's signature for the payout, given to operators off-chain.
/// * `network` - The Bitcoin network.
///
/// # Returns
/// A [`TxHandler`] for the payout transaction, or a [`BridgeError`] if construction fails.
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
