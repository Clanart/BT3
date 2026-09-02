## Analysis

The reported RAACToken/FeeCollector bug is a Solidity token-fee accounting mismatch — not directly applicable to Clementine's Rust Bitcoin bridge. However, the underlying bug class ("the value that actually moved is not the same as the value credited to the accounting record") maps onto Clementine's operator-reimbursement flow through the same class of unauthenticated data (`OP_RETURN` metadata) being trusted as ground truth for "who paid."

### Root cause

`create_payout_txhandler` builds the payout transaction with the user's withdrawal UTXO as input (signed by the user with `SinglePlusAnyoneCanPay`) and writes the *claimed* fronting operator's x-only pubkey into an unsigned `OP_RETURN` output: [1](#0-0) 

Because the user's signature uses `TapSighashType::SinglePlusAnyoneCanPay`, it only commits to input 0 and output 0 — it places no cryptographic constraint on the extra funding inputs or on the `OP_RETURN` output that names the operator: [2](#0-1) [3](#0-2) 

Any party holding the withdrawal's public `(input_outpoint, input_signature)` — data explicitly documented as intended to be shared with (multiple) operators off-chain to compete for the fronting fee — can independently fund the same payout tx from their own wallet, keep the operator's own signing key out of it entirely, and write an *arbitrary* operator's x-only pubkey into `OP_RETURN`.

On the verifier side, `update_finalized_payouts` trusts this unauthenticated `OP_RETURN` field as the identity of the operator who fronted the withdrawal, with no check that the named operator's own funds/signature were used as inputs to the transaction: [4](#0-3) 

This attribution is persisted and later consumed by each operator's own automation, which polls for payouts credited to *its own* key and unconditionally starts the reimbursement kickoff process for them: [5](#0-4) [6](#0-5) 

Neither `update_finalized_payouts` nor `handle_finalized_payout` verifies that the credited operator's own wallet/signature actually funded the payout inputs — the reimbursement (`create_reimburse_txhandler`, paying the full `bridge_amount` from the vault UTXO) proceeds purely because the withdrawal UTXO was spent and the `OP_RETURN` names that operator: [7](#0-6) 

### Binding broken

`operator credited (OP_RETURN pubkey) == party that funded the payout inputs` does not hold. An unprivileged party can front (or even partially front) a withdrawal using their own BTC, name an arbitrary/honest operator in `OP_RETURN`, and cause that operator's own automated node to later claim the full `bridge_amount` reimbursement for a payout it never funded — matching the Critical impact category "an operator reimbursed for a payout it never funded."

---

### Title
Unauthenticated OP_RETURN operator attribution in payout_tx lets any funder misattribute reimbursement credit to an operator who never fronted the withdrawal - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs)

### Summary
`create_payout_txhandler` embeds the "fronting operator" identity in an `OP_RETURN` output that is not covered by the user's `SinglePlusAnyoneCanPay` signature, so it can be set to any value by whoever actually funds and broadcasts the payout transaction. `update_finalized_payouts` in the verifier trusts this field unconditionally to decide which operator is credited for having fronted the withdrawal, and each operator's `payout_checker` task automatically initiates the full reimbursement kickoff flow for payouts attributed to its own key — without ever checking that its own funds paid for the payout.

### Finding Description
The withdrawal-payout protocol is designed so that a user pre-signs their withdrawal input with `SinglePlusAnyoneCanPay`, letting any operator complete the transaction by adding funding inputs and constructing the rest of the transaction [3](#0-2) [8](#0-7) . This signature commits only to the withdrawal input and its corresponding output; it places no constraint on the additional payout-tx outputs, in particular the `OP_RETURN` output carrying the operator's x-only pubkey [1](#0-0) [2](#0-1) .

Consequently, any party in possession of the public `(input_outpoint, input_signature)` pair for a pending Citrea withdrawal can construct and broadcast their own version of the payout transaction: fund it entirely from their own wallet, and place any operator's x-only pubkey (a publicly known value, obtainable via `GetXOnlyPublicKey`) into the `OP_RETURN` output.

Downstream, verifiers scan for payout transactions and extract the "payer" identity purely from this `OP_RETURN` field, with no verification that the named operator's own signature or wallet funded the transaction [4](#0-3) . This attribution is persisted per withdrawal index.

Each operator runs an automated `PayoutCheckerTask` that queries for the first unhandled payout attributed to *its own* xonly pubkey and, upon finding one, unconditionally proceeds to `handle_finalized_payout`, which locates an unused kickoff connector and drives the reimbursement (kickoff → round → reimburse) flow to claim the full `bridge_amount` from the deposit's move-to-vault UTXO [9](#0-8) [6](#0-5) [7](#0-6) . At no point in this reimbursement path is it verified that the credited operator's own bitcoin wallet supplied the inputs that paid the withdrawing user.

### Impact Explanation
An unprivileged party who fronts a withdrawal with their own funds can name any operator in the transaction's `OP_RETURN`. That named operator's own automation will then autonomously trigger its full reimbursement flow and collect the entire `bridge_amount` from the vault UTXO for a payout it never funded. This is a direct instance of "an operator reimbursed for a payout it never funded," breaking the custody binding that vault BTC reimbursement should only flow to the party that actually paid the withdrawing user. It also lets a malicious party consume an honest operator's kickoff connector/collateral for reimbursement events the operator did not initiate, interfering with that operator's own accounting and round progression.

### Likelihood Explanation
Exploitation requires only the public `(input_outpoint, input_signature)` data for a pending withdrawal — data the protocol is explicitly designed to expose to multiple operators to enable competitive fronting — plus enough BTC to fund the payout output and fee. No Clementine gRPC access, operator role, or privileged key is required; the attacker can construct and broadcast the transaction directly to the Bitcoin network.

### Recommendation
Bind the `OP_RETURN` operator attribution cryptographically to the actual funding operator, e.g., by requiring the named operator's own signature over the full transaction (including `OP_RETURN`) rather than relying on an unsigned metadata field, or by having `update_finalized_payouts`/`handle_finalized_payout` verify that the payout transaction's additional funding inputs originate from the credited operator's registered wallet/address before allowing that operator's automation to claim reimbursement.

### Proof of Concept
1. Monitor Citrea's bridge contract for a pending withdrawal, obtaining `(withdrawal_id, input_outpoint, input_signature)` — data intentionally shared off-chain with operators per the withdrawal design [8](#0-7) .
2. Independently build a payout transaction using `create_payout_txhandler` semantics: reuse the user's signed input/output (valid under `SinglePlusAnyoneCanPay`), fund it entirely with attacker-controlled UTXOs, and set the `OP_RETURN` output to an honest operator's known x-only pubkey [1](#0-0) .
3. Broadcast this transaction directly to Bitcoin, spending the withdrawal UTXO registered on Citrea.
4. The verifier's block scanner records the honest operator as the payer based solely on `OP_RETURN` [4](#0-3) .
5. The honest operator's `PayoutCheckerTask` detects an "unhandled payout" credited to itself and autonomously drives the kickoff/reimburse flow, collecting the full `bridge_amount` from the deposit's vault UTXO [9](#0-8)  — reimbursing an operator for a withdrawal payment it never made.

### Citations

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

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };
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

**File:** core/src/verifier.rs (L2312-2342)
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
```

**File:** core/src/task/payout_checker.rs (L41-105)
```rust
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
```

**File:** core/src/operator.rs (L839-885)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

```

**File:** core/src/rpc/clementine.proto (L241-253)
```text
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
}
```
