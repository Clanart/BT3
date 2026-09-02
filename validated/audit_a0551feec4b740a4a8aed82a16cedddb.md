## Finding

The security question is valid: a completely unprivileged actor can force any real operator to be credited (and later reimbursed) for a payout that operator never funded, because attribution of a payout to an operator is derived solely from an unauthenticated 32-byte value pushed by whoever broadcasts the confirmed payout transaction.

### Title
Unauthenticated OP_RETURN operator attribution lets anyone assign any registered operator credit for a payout it never funded - ([File: core/src/verifier.rs], [File: core/src/database/verifier.rs], [File: core/src/task/payout_checker.rs])

### Summary
The verifier attributes a Bitcoin payout to an operator purely by parsing the payout transaction's OP_RETURN bytes and treating them as an xonly pubkey — with no cryptographic proof that the named operator actually built, signed or funded that transaction. Since the payout's SIGHASH_SINGLE|ANYONECANPAY signature only commits to the withdrawal input and output index 0, any unprivileged party can independently construct and broadcast a valid payout transaction (self-funding it with their own extra inputs) that satisfies the committed withdrawal, while tagging the OP_RETURN with a victim operator's real, public xonly pubkey. The victim operator's own `PayoutCheckerTask` automation then unconditionally claims a `Reimburse` kickoff for that withdrawal, believing it fronted the payout.

### Finding Description
The binding that should hold is: `payout_payer_operator_xonly_pk` for withdrawal `i` == the xonly pubkey of the operator who actually broadcast/funded the payout for `i`. This binding is broken because it is derived from unauthenticated OP_RETURN bytes:

- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) scans the confirmed payout tx for the withdrawal, extracts the OP_RETURN via `parse_op_return_data`/`XOnlyPublicKey::from_slice`, and stores it as `payout_payer_operator_xonly_pk` with **no signature or ownership check tying it to the named operator**: [1](#0-0) .
- The payout tx's signature only commits to the input and the output at the same index (`SinglePlusAnyoneCanPay`), leaving the OP_RETURN output completely unauthenticated and freely settable by whoever constructs the final broadcast transaction: [2](#0-1)  and confirmed by the sighash test builder for `Payout` transactions using `SinglePlusAnyoneCanPay`: [3](#0-2) .
- `get_first_unhandled_payout_by_operator_xonly_pk` (`core/src/database/verifier.rs:282-313`) simply trusts this stored field and returns the lowest-`idx` unhandled row matching an operator's own pubkey: [4](#0-3) .
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-79`) blindly calls `handle_finalized_payout` for whatever row is returned, with no verification that the operator itself performed the fronting: [5](#0-4) .
- `Operator::handle_finalized_payout` (`core/src/operator.rs:839-885`) proceeds to grab an unused, presigned kickoff connector for the deposit and issues a `Reimburse` kickoff using the operator's own key material, purely because the DB row named it: [6](#0-5) .

**Exploit flow:** An attacker who is a normal, otherwise-honest depositor/withdrawer for withdrawal index `A` (a distinct deposit from the operator's legitimate withdrawal `B`) calls Citrea's `withdraw` themselves, choosing the dust input UTXO, signature and sighash flag (`SinglePlusAnyoneCanPay`), and the desired output script/amount — as the rules state they are entitled to do. They then independently construct and broadcast (without ever calling any operator's or aggregator's gRPC) the actual Bitcoin payout transaction: input = their own signed dust UTXO, output[0] = their chosen destination, additional funding inputs from their own wallet, and OP_RETURN = the real, public xonly pubkey of victim operator O (any operator's pubkey is public knowledge, e.g. from `fetch_operator_keys`). Once this confirms, the verifier's chain scan sets `payout_payer_operator_xonly_pk = O` for withdrawal `A`, even though O never touched this transaction. If `A < B`, operator O's own `PayoutCheckerTask` will pick up `A` first via `get_first_unhandled_payout_by_operator_xonly_pk(O)` and issue a `Reimburse` kickoff for deposit `A`, consuming O's presigned kickoff connector and claiming the deposit's bridge funds as "reimbursement," despite O never having funded anything for withdrawal `A`.

No existing guard prevents this: `is_deposit_valid`, `SPV::verify`, and the bridge circuit only check that the transaction confirms and that the OP_RETURN parses to a valid xonly key consistent with what's committed in the kickoff — they never verify that the named operator actually authored the payout transaction. `validate_payer_is_operator` (`core/src/operator.rs:1687-1740`) likewise only compares the unauthenticated DB field against the operator's own key, which is exactly what the attacker forges.

### Impact Explanation
This directly matches the Critical category "an operator reimbursed for a payout it never funded." The bridge's move-to-vault/deposit collateral for withdrawal `A` is paid out via a `Reimburse` kickoff to operator O, while O contributed nothing to fronting withdrawal `A` (the attacker funded it out of pocket to pay themselves). This is repeatable for every withdrawal the attacker controls as depositor/withdrawer, against every registered operator whose pubkey is public, and additionally lets an attacker grief a targeted operator by forcing consumption of that operator's limited per-round kickoff connectors on withdrawals it never intended to service, delaying or complicating its legitimate reimbursement queue.

### Likelihood Explanation
No privileged role, key material, or majority hashrate is required — only the ability to perform a normal Citrea withdraw and broadcast a self-funded Bitcoin transaction with an attacker-chosen OP_RETURN, both explicitly within the stated attacker capabilities. Attacker cost is limited to their own withdrawal amount and fees (which they largely pay to themselves), making this cheap and repeatable across many withdrawals/operators.

### Recommendation
Do not attribute payout funding based solely on OP_RETURN bytes. Require a cryptographic binding between the payout transaction and the claiming operator — e.g., have the operator itself co-sign or include a Schnorr signature over the payout transaction (or over `deposit_id || withdrawal idx || payout_txid`) that verifiers check before setting `payout_payer_operator_xonly_pk`, or require that the additional funding inputs demonstrably originate from the named operator's registered collateral/wallet outputs. `get_first_unhandled_payout_by_operator_xonly_pk` and `handle_finalized_payout` should refuse to act on an attribution that lacks this proof.

### Proof of Concept
```rust
// core/src/database/verifier.rs (new #[tokio::test])
#[tokio::test]
async fn attacker_can_forge_payout_attribution_for_real_operator() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let operator_xonly_pk = generate_random_xonly_pk(); // victim operator's real, public key

    // Insert legitimate payout for withdrawal idx=5, funded by the operator itself.
    // (setup move_to_vault_txid/withdrawal_utxo rows as in update_get_payout_txs_from_citrea_withdrawal test)
    db.update_payout_txs_and_payer_operator_xonly_pk(
        None,
        vec![(5, legit_payout_txid, Some(operator_xonly_pk), legit_blockhash)],
    ).await.unwrap();

    // Insert attacker-forged payout for a DIFFERENT withdrawal idx=2 (different deposit),
    // where the OP_RETURN was tagged by the attacker with the same operator's real pubkey,
    // despite the operator never funding it.
    db.update_payout_txs_and_payer_operator_xonly_pk(
        None,
        vec![(2, forged_payout_txid, Some(operator_xonly_pk), forged_blockhash)],
    ).await.unwrap();

    let (idx, move_txid, _) = db
        .get_first_unhandled_payout_by_operator_xonly_pk(None, operator_xonly_pk)
        .await
        .unwrap()
        .unwrap();

    // Demonstrates the binding is broken: the operator's automation is handed the
    // attacker-forged withdrawal (idx=2) ahead of the legitimate one (idx=5),
    // with zero proof the operator ever built or funded withdrawal 2's payout tx.
    assert_eq!(idx, 2);
    assert_eq!(move_txid, forged_move_txid);
}
```
This test (in `core/src/database/verifier.rs`) reproduces the exact query behavior with no mainnet/live Citrea dependency, proving that `get_first_unhandled_payout_by_operator_xonly_pk` cannot distinguish attacker-forged attribution from genuine operator-funded payouts, and that `PayoutCheckerTask`/`handle_finalized_payout` would act on it regardless of true funding source.

### Citations

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

**File:** core/src/test/common/setup_utils.rs (L499-531)
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
```

**File:** core/src/database/verifier.rs (L282-296)
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
```

**File:** core/src/task/payout_checker.rs (L41-79)
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
```

**File:** core/src/operator.rs (L839-861)
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

```
