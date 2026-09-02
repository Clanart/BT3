### Title
Payout tx OP_RETURN operator attribution is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing a third party to redirect operator reimbursement credit - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The user's withdrawal-authorization signature on a `Payout` transaction only commits to input 0 and output 0 via `TapSighashType::SinglePlusAnyoneCanPay`, leaving the anchor output and the OP_RETURN output (which encodes the operator's x-only pubkey that "fronts" the withdrawal) completely unsigned by the user and unauthenticated by the credited operator. Anyone observing an honest operator's unconfirmed `Payout` tx can rebroadcast a replacement that reuses the same input 0 witness and output 0, but substitutes the OP_RETURN's operator pubkey and funds the rest from their own wallet, causing `Verifier::update_finalized_payouts` to attribute the completed withdrawal to a different, uninvolved operator, who is then reimbursed by `PayoutCheckerTask`/`Operator::handle_finalized_payout`.

### Finding Description
The claimed binding is: `payer_operator_xonly_pk (written unsigned into OP_RETURN output 2, parsed by parse_op_return_data and stored via update_payout_txs_and_payer_operator_xonly_pk) == the party who actually funded output 0 of the confirmed Payout tx`.

Tracing the code:
- `create_payout_txhandler` builds outputs `[user_payout, anchor, OP_RETURN(operator_xonly_pk)]` and only puts the user's key-spend witness on input 0 [1](#0-0) .
- `Operator::withdraw` verifies the user's signature against `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` [2](#0-1) .
- `calculate_pubkey_spend_sighash` uses `Prevouts::One` for `SinglePlusAnyoneCanPay`/related flags, and `taproot_key_spend_signature_hash` for `SIGHASH_SINGLE` semantics commits only to the output at the *same index* as the signed input (index 0) — the anchor (index 1) and OP_RETURN (index 2) outputs are not part of the sighash preimage at all [3](#0-2) .
- `parse_withdrawal_sig_params` only enforces that the sighash type is `SinglePlusAnyoneCanPay`, confirming this is the expected/accepted signature shape for third-party funded construction [4](#0-3) .
- Confirmation-time attribution: `Verifier::update_finalized_payouts` reads the confirmed payout tx from the block, extracts the OP_RETURN via `get_first_op_return_output`, parses it with `parse_op_return_data`, and stores the resulting `operator_xonly_pk` as the payer of that withdrawal with no further cryptographic check that this pubkey corresponds to whoever actually paid [5](#0-4) .
- `PayoutCheckerTask::run_once` looks up unhandled payouts strictly by the stored `operator_xonly_pk` (`self.operator.signer.xonly_public_key`) and, if found, calls `Operator::handle_finalized_payout`, marking it handled and triggering the reimbursement flow (kickoff, later `create_reimburse_txhandler` paying the full `bridge_amount` from the move-to-vault UTXO) [6](#0-5) [7](#0-6) .

Because outputs 1 and 2 are outside the signed message, any party who can construct a replacement transaction with the same input 0 witness, the same output 0, but a substituted OP_RETURN operator pubkey and self-funded remaining inputs/fees, produces a transaction that is equally valid from the user-signature perspective. If this replacement confirms instead of the honest operator's original broadcast, the bridge will credit and eventually reimburse an operator who never funded the withdrawal, breaking the informal attribution invariant "the operator credited for withdrawal i is the party whose funds paid output 0."

No existing guard blocks this: `SECP.verify_schnorr` in `Operator::withdraw` only checks input 0/output 0 [8](#0-7) ; `update_finalized_payouts` performs no signature or provenance check on the OP_RETURN field, it is treated as trusted plaintext [9](#0-8) ; and `PayoutCheckerTask` blindly trusts the DB-stored attribution.

### Impact Explanation
This matches the listed Critical category "an operator reimbursed for a payout it never funded." The named (uninvolved) operator receives the full `bridge_amount` reimbursement from the deposit's move-to-vault UTXO without having spent their own capital on the withdrawal, while the party who actually funded the withdrawal (the attacker, or the original honest operator whose broadcast was pre-empted) receives no credit. However, per the constraints of this audit (unprivileged attacker only, no collusion/social-engineering credit), the only party who benefits value-wise from this substitution is the substituted (uninvolved, pre-existing, legitimately-registered) operator — an entity the attacker cannot control or redeem funds through themselves, since redemption requires the full operator round/kickoff/collateral infrastructure that an unprivileged attacker does not possess. The attacker's net effect per attempt is to spend their own BTC funding the withdrawal output and fees while directing bridge-side credit to an arbitrary third-party operator, and to deny (front-run) the original honest operator's reimbursement claim. This is a genuine attribution/authentication gap (worth fixing) but does not, on its own and absent operator collusion (out of scope), give the unprivileged attacker any extractable value from the bridge beyond what a normal withdrawal already releases once.

### Likelihood Explanation
Preconditions are realistic: a withdrawal must be registered on Citrea, and the honest operator's `Payout` tx must be unconfirmed and observable in the mempool (which is public), which is a normal, frequent occurrence in the intended flow. Constructing the replacement transaction requires only public-domain data (the visible input-0 witness and output 0) plus the attacker's own BTC to cover output 0's value and fees, and winning a first-seen/RBF mempool race. This is a feasible mempool-level operation for any Bitcoin-transacting party, but the attacker bears real, non-trivial cost (must front the entire withdrawal amount from their own funds) for a benefit that accrues to a third party they generally do not control, which sharply limits real-world incentive to repeatedly execute this absent collusion.

### Recommendation
Bind the OP_RETURN operator-attribution output to the same signature that authorizes the withdrawal, e.g. by having `Operator::withdraw` sign (or otherwise cryptographically commit to) all three outputs when constructing/broadcasting the `Payout` transaction, or by requiring the operator's own signature/commitment over the OP_RETURN payload (in addition to the user's `SinglePlusAnyoneCanPay` signature) so that a third party cannot alter the credited operator identity without invalidating the whole transaction. Alternatively, have `update_finalized_payouts`/`handle_finalized_payout` cross-verify that the party funding output 0 (via its additional inputs) is provably associated with the credited operator's known wallet/keys before marking a payout as handled.

### Proof of Concept
```
cargo test -p core payout_op_return_attribution_swap -- --nocapture
```
Test outline (would need to be written against `core/src/test` harness, currently out of scope per the excluded test paths but illustrating the binding check):
1. Register a withdrawal on the (mock) Citrea client for `deposit_id = i`, matching `withdrawal_utxo`.
2. Build the honest operator's `Payout` tx via `create_payout_txhandler` with `operator_xonly_pk = honest_op_pk`, broadcast but do not confirm.
3. Reuse `payout_tx.input[0].witness` (S+AP signature) and `payout_tx.output[0]` verbatim; construct a new tx adding attacker-funded inputs/fee output, and a new OP_RETURN output with `uninvolved_op_pk` instead of `honest_op_pk`; broadcast and mine only this version.
4. Run `Verifier::update_finalized_payouts` (via block sync) and assert `database::verifier::get_payout_info_from_move_txid` / DB row for withdrawal `i` returns `uninvolved_op_pk`, not `honest_op_pk` — this is the "before/after" assertion on the broken ATTRIBUTION equality.
5. Run `uninvolved_op`'s `PayoutCheckerTask::run_once` and assert it returns `Ok(true)` and produces a `kickoff_txid`, proving the uninvolved operator's reimbursement flow was triggered for a payout it never funded.

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

**File:** core/src/builder/transaction/txhandler.rs (L210-233)
```rust
    pub fn calculate_pubkey_spend_sighash(
        &self,
        txin_index: usize,
        sighash_type: TapSighashType,
    ) -> Result<TapSighash, BridgeError> {
        let prevouts_vec: Vec<&TxOut> = self
            .txins
            .iter()
            .map(|s| s.get_spendable().get_prevout())
            .collect();
        let mut sighash_cache: SighashCache<&bitcoin::Transaction> =
            SighashCache::new(&self.cached_tx);
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
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

**File:** core/src/task/payout_checker.rs (L39-111)
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

        dbtx.commit().await?;

        Ok(true)
    }
```
