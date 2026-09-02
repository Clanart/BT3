### Title
Payout `OP_RETURN` operator attribution is unauthenticated, letting any observer hijack reimbursement credit for a withdrawal it did not front - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` writes the fronting operator's x-only pubkey into an `OP_RETURN` output of the payout transaction, and the chain-sync logic later trusts that `OP_RETURN` value as ground truth for "who paid the withdrawal" (`payout_payer_operator_xonly_pk`). That field is not covered by the user's withdrawal signature and is not otherwise authenticated on-chain, so it is exactly the `_to`-vs-`msg.sender` class of bug described in the report: the party credited with having fronted the payout is whatever value is written into an unauthenticated field, not the party whose keys/funds actually paid the user.

### Finding Description
`create_payout_txhandler` builds the payout tx with three outputs: the user payout (index 0), an anchor (index 1), and an `OP_RETURN` carrying `operator_xonly_pk` (index 2): [1](#0-0) 

The user only signs with a `SinglePlusAnyoneCanPay`-style signature (per the RPC doc comments), which under BIP341 covers the spent input and only the *single output at the same index* as that input - i.e. output 0 (the user payout). The anchor and `OP_RETURN` outputs are outside the commitment of that signature entirely: [2](#0-1) 

Downstream, the chain-sync task (`update_finalized_payouts`) reads whatever bytes are in that `OP_RETURN` output directly from the confirmed transaction and stores it as the "payer" of the withdrawal, with no cryptographic check that the named key actually authorized or funded the payout: [3](#0-2) 

This value is then used as the sole basis for the reimbursement pipeline: `get_first_unhandled_payout_by_operator_xonly_pk` selects payouts for automation strictly by matching this stored key against the local operator's own key, and `PayoutCheckerTask::run_once` will automatically drive `handle_finalized_payout` -> kickoff -> reimbursement for any payout attributed to it: [4](#0-3) [5](#0-4) 

The only consistency check performed later, `is_kickoff_malicious`, merely verifies that the `OP_RETURN` operator key equals the key of whoever *sends the kickoff* - it does not verify that the `OP_RETURN` key's owner is the party who actually funded/broadcast the original payout transaction: [6](#0-5) 

Because the `OP_RETURN` output sits outside the signature's commitment and the transaction uses a non-standard V3 version (RBF-friendly, ephemeral-anchor style) construction, once a legitimate operator's payout transaction becomes visible (e.g. in the mempool) prior to confirmation, its exact `input`/`signature`/output-0 triple can be copied into an alternate transaction that pays the identical user output (satisfying the `SinglePlusAnyoneCanPay` commitment) but substitutes a *different* x-only pubkey into the `OP_RETURN`. If that replacement is broadcast/confirmed first (via RBF/higher fee, permitted by the `DEFAULT_SEQUENCE`/anchor-output CPFP design), the chain-sync task will attribute the payout to the attacker-chosen operator's key instead of the operator that actually fronted the withdrawal. That operator's own automation will then treat the withdrawal as its own unhandled payout and autonomously proceed through kickoff and reimbursement, drawing the deposit's `bridge_amount` out of the vault to itself for a withdrawal it never funded.

### Impact Explanation
This breaks the core custody binding "the operator credited with reimbursement" = "the party that actually fronted the withdrawal." An attacker (any unprivileged party, including a rival operator) who observes a valid pending payout transaction can rewrite the unauthenticated `OP_RETURN` attribution and cause the bridge to reimburse `bridge_amount` to an operator that never funded the withdrawal, while the actual funder receives no credit. This matches the Critical impact class: "an operator reimbursed for a payout it never funded" (and correspondingly the honest funding operator is denied reimbursement it is owed), causing real BTC to leave the vault detached from the party that actually performed the fronting.

### Likelihood Explanation
Exploitation requires only observing an in-flight, unconfirmed payout transaction (which is visible in the mempool before finality/confirmation, a normal window in Bitcoin) and rebroadcasting a fee-bumped variant with a modified `OP_RETURN`. No verifier, operator, or aggregator privilege is needed to construct or broadcast such a transaction - any actor with mempool visibility and the ability to relay a transaction can attempt it. The main uncertainty (not fully verifiable from the index alone) is whether the RBF/finality window and other operators' fee-bumping/monitoring in practice give an attacker a reliable window to win the race; this depends on the specific `TxSender`/RBF implementation details in `crates/clementine-tx-sender`, which were only partially inspected.

### Recommendation
Do not trust the `OP_RETURN` payer attribution as authoritative. Either (a) bind the operator attribution cryptographically into the signature commitment (e.g., require the operator's own signature/commitment over the `OP_RETURN` payload, not just the user's single-output signature), or (b) require the RPC-level self-attestation path (`self.signer.xonly_public_key`, as done in `operator.rs`'s `withdraw`) to be the only source of truth, and treat any raw/observed payout transaction whose `OP_RETURN` cannot be tied to a signature from the claimed operator as unattributed (`None`), rather than crediting the named key.

### Proof of Concept
1. Operator A calls `withdraw`/`internal_withdraw`, producing and broadcasting a payout transaction fronting withdrawal `W` with `OP_RETURN = A_xonly_pk`, per `core/src/operator.rs` (`create_payout_txhandler` call) and `core/src/builder/transaction/operator_reimburse.rs:407-436`.
2. Before this transaction confirms, an attacker observes it in the mempool. Because the `SinglePlusAnyoneCanPay` signature only commits to input 0 and output 0 (the user's payout), the attacker constructs an alternate transaction reusing the same input/signature/output-0 but replacing the `OP_RETURN` output with `B_xonly_pk` (an arbitrary operator B's key), and rebroadcasts it with a higher fee (leveraging the RBF-friendly `NON_STANDARD_V3`/anchor design).
3. If the attacker's version confirms first, `update_finalized_payouts` (`core/src/verifier.rs:2311-2350`) records `payout_payer_operator_xonly_pk = B_xonly_pk` for withdrawal `W`.
4. Operator B's own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-106`) detects an "unhandled payout" attributed to itself and automatically drives `handle_finalized_payout`, kickoff, and reimbursement, receiving `bridge_amount` from the vault for a withdrawal it never funded, while operator A - who actually paid the user - is never credited.

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

**File:** core/src/rpc/clementine.proto (L390-405)
```text
  // Prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned. Note: This is intended for
  // operator's own use, so it doesn't include a signature from aggregator.
  rpc InternalWithdraw(WithdrawParams) returns (RawSignedTx) {}

  // First, if verification address in operator's config is set, the signature
  // in rpc is checked to see if it was signed by the verification address. Then
  // prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned.
  rpc Withdraw(WithdrawParamsWithSig) returns (RawSignedTx) {}
```

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

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

**File:** core/src/verifier.rs (L2311-2350)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
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

**File:** core/src/task/payout_checker.rs (L39-106)
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
