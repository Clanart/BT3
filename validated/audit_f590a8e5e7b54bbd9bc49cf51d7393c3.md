Now I have confirmation on the crucial fact: `create_reimburse_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:341-385`) pays out the operator an amount equal to the **entire MoveToVault deposit value** (`bridge_amount`), sourced by spending the deposit's own `MoveToVaultTx` UTXO (`UtxoVout::DepositInMove`) — this is real depositor/bridge money, not operator's own recycled collateral: [1](#0-0) 

This changes the picture materially: getting a kickoff/Reimburse tx to succeed for operator O actually **unlocks the deposit's real bridge funds** to O's `reimburse_addr`, regardless of who actually funded the corresponding `Payout` tx. Combined with the earlier findings:

1. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:31-113`) blindly trusts `get_first_unhandled_payout_by_operator_xonly_pk` — it triggers `handle_finalized_payout` for **any** payout attributed to O in the DB, with no check that O itself broadcast/funded that `Payout` transaction. [2](#0-1) 

2. `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) attributes `payout_payer_operator_xonly_pk` purely by parsing the OP_RETURN bytes of the on-chain `Payout` tx, with zero cryptographic binding to O. [3](#0-2) 

3. The OP_RETURN output is **output index 2** in `create_payout_txhandler`, while the user's signature over the withdrawal input uses `SinglePlusAnyoneCanPay`, which per BIP-341/taproot sighash rules only commits to input 0 and the single corresponding output at the *same index* (output 0). Outputs 1 (anchor) and 2 (OP_RETURN) are **not covered** by the signature, and `ANYONECANPAY` permits arbitrary additional inputs. [4](#0-3) [5](#0-4) [6](#0-5) 

4. `send_asserts` and `is_kickoff_malicious` only check `payout_op_xonly_pk == kickoff_data.operator_xonly_pk` — both values are attacker/OP_RETURN-controlled and self-consistent by construction, so this check never catches the forgery. [7](#0-6) [8](#0-7) 

### Title
Unauthenticated OP_RETURN operator-attribution in Payout tx lets anyone credit an arbitrary operator for a self-funded withdrawal, unlocking bridge funds via that operator's kickoff/Reimburse - ([File: core/src/builder/transaction/operator_reimburse.rs], [core/src/verifier.rs], [core/src/task/payout_checker.rs])

### Summary
The `Payout` transaction's OP_RETURN output (which records which operator "fronted" a withdrawal) is outside the coverage of the withdrawing user's `SIGHASH_SINGLE|ANYONECANPAY` signature, and is trusted verbatim by `update_finalized_payouts` with no check that the named operator actually signed/broadcast/funded the transaction. Since `PayoutCheckerTask` automatically triggers `handle_finalized_payout`/kickoff for any DB row attributed to an operator's pubkey, an attacker who is themselves the withdrawing Citrea user can self-fund the entire `Payout` tx and attribute it to a victim operator O, causing O's automation to run the kickoff/assert flow and eventually spend `create_reimburse_txhandler`'s `MoveToVaultTx` output — real bridge funds — to O's `reimburse_addr`, for a payout O never funded.

### Finding Description
The broken binding: the value used to authorize reimbursement (`payout_payer_operator_xonly_pk` parsed from the on-chain `Payout` tx's OP_RETURN, `core/src/verifier.rs:2319-2321`) is treated as equivalent to "operator O actually called `withdraw()`/fronted this payout" — but nothing enforces that equality.

Path:
- Attacker (as the legitimate Citrea withdrawing user) registers a withdrawal on the Citrea Bridge contract, choosing the withdrawal UTXO's script pubkey (their own key) and later signs it themselves with `SinglePlusAnyoneCanPay`.
- Because `calculate_pubkey_spend_sighash` for `SinglePlusAnyoneCanPay` only commits to input 0's prevout and the single output at index 0 (`core/src/builder/transaction/txhandler.rs:222-233`, confirmed against BIP-341 encoding in `circuits-lib/src/bridge_circuit/mod.rs:750-810`), the attacker can freely construct the full `Payout` transaction themselves: input 0 = their own signed withdrawal UTXO, additional attacker-funded inputs (allowed by `ANYONECANPAY`), output 0 = their own payout destination, output 2 = OP_RETURN naming victim operator O's xonly pubkey instead of their own.
- The attacker broadcasts this self-funded tx directly to Bitcoin — no operator RPC call is needed.
- `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) parses the OP_RETURN and stores `payout_payer_operator_xonly_pk = O` with no signature/ownership check.
- Operator O's own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-60`) picks this up via `get_first_unhandled_payout_by_operator_xonly_pk(O)` and calls `handle_finalized_payout`, which signs and sends O's real `Kickoff` transaction (using O's own private key, since O's automation blindly trusts the DB attribution).
- `send_asserts` (`core/src/operator.rs:1284-1295`) and `is_kickoff_malicious` (`core/src/verifier.rs:1882-1890`) both re-derive/compare `payout_op_xonly_pk` against `kickoff_data.operator_xonly_pk`, which are consistent by construction (O's automation set both), so neither guard detects the forgery.
- Assuming the kickoff survives the challenge window (which it will, since the withdrawal genuinely happened on Citrea and the payout tx/blockhash genuinely exist), `create_reimburse_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:341-385`) pays the full `MoveToVaultTx` deposit value to O's `reimburse_addr` — spending actual bridge/depositor collateral, not merely returning O's own bond.

### Impact Explanation
This matches the Critical category "an operator reimbursed for a payout it never funded." Concretely: real deposited bridge BTC (the `MoveToVaultTx` output, equal to `bridge_amount`) is released to operator O's `reimburse_addr` even though O never called `withdraw()`, never signed a payout tx, and never fronted any funds — the entire withdrawal was paid for by the attacker to themselves. This consumes one of O's limited per-round kickoff slots and forces O through the assert/disprove game for a payout O didn't choose, while releasing bridge collateral to O for free. It is repeatable per finalized withdrawal/deposit and works against any operator whose xonly pubkey the attacker chooses to write into the OP_RETURN, as long as that operator has automation enabled (`PayoutCheckerTask` running).

### Likelihood Explanation
Preconditions: attacker must be able to (a) obtain a withdrawal registration on the Citrea bridge contract for their own funds (an ordinary user action, already in the threat model), (b) know/choose the withdrawal UTXO and sign it themselves with `SinglePlusAnyoneCanPay`, and (c) fund the rest of the `Payout` transaction with their own BTC (paid right back to themselves as the withdrawal output) plus network fees. No verifier, operator, or aggregator key/collateral is needed, and no cooperation from O is required. The target operator must have `automation`/`PayoutCheckerTask` enabled, which is the standard configuration for operators expecting to process payouts and be reimbursed. This is straightforward to reproduce on regtest with `cargo test`.

### Recommendation
Do not trust the payout OP_RETURN as sole proof of operator attribution. Bind the OP_RETURN operator field (and ideally the whole `Payout` tx) to a signature the withdrawing user could only produce for a transaction the operator explicitly requested/co-signed — e.g., require the operator xonly pubkey to be covered by the user's committed sighash (use `SIGHASH_ALL`/`SIGHASH_SINGLE` without `ANYONECANPAY`, or otherwise commit the OP_RETURN bytes into the signed message), so a third party cannot rewrite the operator attribution after the fact. Additionally, `PayoutCheckerTask`/`handle_finalized_payout` should cross-check that the operator's own wallet/db actually recorded initiating this specific payout (e.g., via a locally-stored record created at `withdraw()`-call time) before starting the kickoff/reimbursement flow for it.

### Proof of Concept
`cargo test` plan (regtest, no mainnet, no live Citrea — use `MockCitreaClient`):
1. Set up two operators, O (victim, automation enabled) and none for the attacker. Perform a normal deposit so a `MoveToVaultTx` exists.
2. Register a withdrawal via the mock Citrea client for an "attacker-controlled" UTXO/address (attacker holds the corresponding secret key), matching `get_withdrawal_utxo_from_citrea_withdrawal`.
3. Manually build the `Payout` transaction using `create_payout_txhandler`-equivalent logic but constructed directly by the "attacker": input 0 = attacker's own withdrawal UTXO, additional attacker-funded input(s) for fees/dust, output 0 = attacker's own address for the withdrawal amount, output 1 = anchor, output 2 = OP_RETURN with **O's** xonly pubkey (not the attacker's). Sign input 0 with `SinglePlusAnyoneCanPay` using the attacker's own key.
4. Broadcast this transaction directly via `rpc.send_raw_transaction`, without ever calling O's `withdraw`/`internal_withdraw`/`Withdraw` RPC.
5. Mine blocks past finality depth; wait for `update_finalized_payouts` to run and assert in the `withdrawals` table that `payout_payer_operator_xonly_pk == O` (`db.get_payout_info_from_move_txid`).
6. Wait for O's `PayoutCheckerTask` (or manually invoke `operator.handle_finalized_payout`) and assert that `send_asserts`/kickoff succeeds for O (no error from the `payout_op_xonly_pk != kickoff_data.operator_xonly_pk` check) — i.e., assert a `Kickoff` tx for O appears on chain and is not rejected by `is_kickoff_malicious`.
7. Progress through challenge timeout / round advance and assert the `Reimburse` tx for O confirms, paying `bridge_amount` to O's `reimburse_addr`, while asserting O's `withdraw()`/`internal_withdraw` RPC was never called during the whole test (no operator-side spend of operator wallet funds occurred for this withdrawal).

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

**File:** core/src/task/payout_checker.rs (L39-60)
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L800-810)
```rust

    if sighash != TapSighashType::None && sighash != TapSighashType::Single {
        // Manually compute sha_outputs
        let mut enc_outputs = sha256::Hash::engine();
        for txout in tx.output.iter() {
            txout.consensus_encode(&mut enc_outputs).expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_outputs)
            .consensus_encode(writer)
            .expect(expect_msg);
    }
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
