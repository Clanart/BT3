### Title
Payout OP_RETURN `operator_xonlypk` is unauthenticated (uncovered by the withdrawal signature), letting anyone attribute a Bitcoin payout to an arbitrary operator and trigger a free reimbursement - (File: `core/src/builder/transaction/operator_reimburse.rs`, `core/src/task/payout_checker.rs`, `core/src/verifier.rs`)

### Summary
The user's withdrawal authorization signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to input 0 and output 0 of the `Payout` transaction; the OP_RETURN output (index 2) carrying `operator_xonly_pk` is never covered by any signature. Anyone who knows this signature — including the withdrawing user themselves, who is free to create it — can build and broadcast their own `Payout` transaction that fronts the withdrawal with self-supplied funding inputs while naming an arbitrary, unrelated operator's real xonly pubkey in the OP_RETURN. That operator's own automation (`PayoutCheckerTask`) will then treat the payout as its own and automatically trigger a `Kickoff`/`Reimburse` flow, draining vault collateral for a payout it never funded.

### Finding Description
The claimed binding is: `deposit_constant.operator_xonlypk == the operator whose own funds actually paid the withdrawal output`.

In practice:
- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the `Payout` tx with input 0 = withdrawal UTXO (`SpendPath::KeySpend`), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(`operator_xonly_pk`).
- The only signature covering input 0 is `TapSighashType::SinglePlusAnyoneCanPay` (enforced in `parser::operator::parse_withdrawal_sig_params`, `core/src/rpc/parser/operator.rs:161-203`, and in the test helper `sign_withdrawal_output`, `core/src/test/common/setup_utils.rs:499-543`). SIGHASH_SINGLE only commits to the output at the *same index* as the signed input (index 0), and ANYONECANPAY lets arbitrary additional inputs be attached. Consequently the OP_RETURN at output index 2 (and any extra funding inputs added later via `fund_raw_transaction`, see `Operator::withdraw`, `core/src/operator.rs:560-675`) is completely outside the signed message.
- Since the withdrawing user creates `in_signature`/`in_outpoint` themselves (an unprivileged attacker action explicitly allowed by the rules: "choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag"), the attacker can build their own `Payout` transaction reusing the same signed input/output pair, add their own funding inputs to cover the value, and attach an OP_RETURN naming ANY operator's real (public) xonly pubkey — not necessarily their own.
- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) blindly parses this OP_RETURN from the first confirmed on-chain tx that spends the registered withdrawal outpoint and stores `operator_xonly_pk` in the DB with no check that this key's owner supplied any of the transaction's value.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-51`) is run per-operator and simply polls `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)`. If the attacker named that operator, its own node will find this payout and automatically call `handle_finalized_payout` → send `Kickoff` → eventually `Reimburse`, with no requirement that its wallet paid the funding inputs of that specific `Payout` tx.
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) only checks `operator_xonly_pk (from OP_RETURN) == kickoff_data.operator_xonly_pk (from kickoff signer)`. Since the framed operator's own kickoff naturally carries its own key, this check passes — there is no independent verification that the framed operator's wallet actually funded the extra payout inputs.
- The bridge circuit (`circuits-lib/src/bridge_circuit/mod.rs:206-229`, `bridge-circuit-host/src/structs.rs:482-516`) faithfully binds `deposit_constant`/`journal_hash` to whatever `operator_xonlypk` is embedded in the payout OP_RETURN — it never checks that this key's owner supplied the payout's funding inputs. SPV correctness (path-only, not real-world attribution) does not help here since the circuit's job is only to prove *which bytes were mined*, not *who paid*.

### Impact Explanation
An unprivileged attacker can force an arbitrary, uninvolved operator's automation (`PayoutCheckerTask`) to believe it fronted a withdrawal it never funded. That operator will then automatically send `Kickoff`/`Reimburse` and receive its collateral back from the `MoveToVault` UTXO (`create_reimburse_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:341-385`) for a payout it did not pay for — vault BTC leaves without a corresponding fronted withdrawal from that operator, i.e., "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" / "an operator reimbursed for a payout it never funded" (Critical, per the rules). This is repeatable across every deposit and every operator whose xonly pubkey is public knowledge (all operator keys are public), and requires no special access beyond broadcasting a Bitcoin transaction.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to be the party that generates the withdrawal request (creating the `SinglePlusAnyoneCanPay` signature themselves, which the protocol explicitly allows any unprivileged withdrawer to do) and enough BTC to cover the withdrawal amount plus fees for their own competing `Payout` transaction (the same cost an honest operator would pay). No majority hashrate, no key compromise, and no privileged role are required — this fully matches the allowed unprivileged attacker capabilities in the rules. Any deployment running the default automation (`PayoutCheckerTask`) is affected.

### Recommendation
Bind `operator_xonly_pk` to the actual funder of the payout transaction cryptographically, e.g.:
- Require the operator's own Schnorr signature over the whole `Payout` transaction (including the OP_RETURN output) as an additional required input/commitment, instead of relying purely on the user's `SinglePlusAnyoneCanPay` signature which leaves the OP_RETURN unauthenticated.
- Alternatively, when recording `operator_xonly_pk` in `update_finalized_payouts`, cross-check that at least one of the additional payout tx inputs' `scriptPubkey` corresponds to the named operator's known collateral/reimbursement address, before trusting the attribution used later by `PayoutCheckerTask` and `is_kickoff_malicious`.

### Proof of Concept
```rust
// core/src/test/... (new test)
// 1. Register a withdrawal (dust UTXO + SinglePlusAnyoneCanPay signature) as the
//    "attacker" role using generate_withdrawal_transaction_and_signature (setup_utils.rs).
// 2. Build the Payout transaction handler via create_payout_txhandler using
//    honest_operator.xonly_public_key (an operator that never sees this tx) instead of
//    the attacker's own key.
// 3. Have the attacker's wallet (not the honest operator) fund the tx (fund_raw_transaction
//    equivalent), sign extra inputs with the attacker's key, and broadcast it, spending the
//    registered withdrawal outpoint.
// 4. Mine the tx to finality; call verifier's update_finalized_payouts logic and assert:
//      stored_operator_xonly_pk == honest_operator.xonly_public_key
//    even though honest_operator supplied zero inputs/signatures to the transaction.
// 5. Run PayoutCheckerTask for honest_operator and assert it returns an "unhandled payout"
//    for this withdrawal, proving the operator's automation would proceed to Kickoff/Reimburse
//    for a payout it never funded — i.e. deposit_constant's operator_xonlypk binding is broken.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** core/src/rpc/parser/operator.rs (L161-203)
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

    let input_outpoint: OutPoint = params
        .input_outpoint
        .ok_or_else(error::input_ended_prematurely)?
        .try_into()?;

    let users_intent_script_pubkey = ScriptBuf::from_bytes(params.output_script_pubkey);

    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
}
```

**File:** core/src/test/common/setup_utils.rs (L499-543)
```rust
fn sign_withdrawal_output(
    config: &BridgeConfig,
    dust_utxo: &UTXO,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (bitcoin::TxOut, taproot::Signature) {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);
    let txin = builder::transaction::input::SpendableTxIn::new(
        dust_utxo.outpoint,
        dust_utxo.txout.clone(),
        vec![],
        None,
    );
    let txout = bitcoin::TxOut {
        value: withdrawal_amount,
        script_pubkey: withdrawal_address.script_pubkey(),
    };
    let unspent_txout = builder::transaction::output::UnspentTxOut::from_partial(txout.clone());

    let tx = builder::transaction::TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            builder::transaction::DEFAULT_SEQUENCE,
        )
        .add_output(unspent_txout.clone())
        .finalize();

    let sighash = tx
        .calculate_sighash_txin(0, sighash::TapSighashType::SinglePlusAnyoneCanPay)
        .expect("Failed to calculate sighash");

    let sig = signer
        .sign_with_tweak_data(sighash, builder::sighash::TapTweakData::KeyPath(None), None)
        .expect("Failed to sign");

    let sig = taproot::Signature {
        signature: sig,
        sighash_type: sighash::TapSighashType::SinglePlusAnyoneCanPay,
    };

    (txout, sig)
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

**File:** core/src/verifier.rs (L2312-2343)
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```
