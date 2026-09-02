## Analysis

Confirmed: the payout tx's user signature uses `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params` [1](#0-0) . This sighash type only commits to input 0 (the withdrawal UTXO) and its corresponding single output (the user payout output) — it does **not** cover the OP_RETURN output that names the operator. `create_payout_txhandler` builds the OP_RETURN with an `operator_xonly_pk` argument that is a plain, unauthenticated function parameter [2](#0-1) . Because `ANYONECANPAY|SINGLE` leaves all other inputs/outputs unsigned, whoever actually assembles and broadcasts the final payout transaction can freely choose which operator's x-only pubkey goes into that OP_RETURN, regardless of who supplied the withdrawal-output funds.

On the credit side, `update_finalized_payouts` blindly trusts this on-chain OP_RETURN and writes it as `payout_payer_operator_xonly_pk` in the `withdrawals` table [3](#0-2) . `PayoutCheckerTask` then picks up "unhandled payouts" filtered only by matching this stored xonly_pk against the local operator's own key and immediately drives reimbursement (`handle_finalized_payout` → kickoff → round-based reimbursement) [4](#0-3) . Nothing in this path checks that the credited operator actually broadcast/funded the payout output — `is_kickoff_malicious` only cross-checks the OP_RETURN pubkey against the *kickoff*'s claimed operator, not against who paid [5](#0-4) .

This is a direct binding violation: "the operator credited" (via OP_RETURN, chosen by whoever broadcasts) is decoupled from "the party that paid" (whoever actually funded the withdrawal output), matching the PoolTogether bug-class of a self-serving default/attribution mechanism that isn't bound to the actual actor.

### Title
Unauthenticated OP_RETURN operator attribution in payout transactions allows crediting reimbursement to an operator that never funded the withdrawal - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs)

### Summary
The payout transaction's user signature uses `SIGHASH_SINGLE|ANYONECANPAY`, which only commits to the withdrawal input and its matching output. The OP_RETURN output that names the "paying" operator is outside this commitment and can be set to any operator's x-only pubkey by whoever constructs/broadcasts the final transaction. Verifiers ingest this OP_RETURN value directly into `payout_payer_operator_xonly_pk` without verifying the named operator actually supplied the payout funds, and the operator's own automation (`PayoutCheckerTask`) will then autonomously claim reimbursement for a withdrawal it never fronted.

### Finding Description
`parse_withdrawal_sig_params` enforces that the user's off-chain signature is `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) . This sighash flag means the signature is valid for *any* transaction that spends the same withdrawal input and pays the committed single output — additional inputs (fee funding) and additional outputs (anchor, OP_RETURN) are entirely unconstrained by the signature.

`create_payout_txhandler` places the "paying" operator's xonly pubkey into an OP_RETURN output as a raw constructor argument with no cryptographic tie to who actually funds the payout output [2](#0-1) .

When the payout confirms on-chain, `update_finalized_payouts` parses this OP_RETURN and stores whatever xonly pubkey is found as `payout_payer_operator_xonly_pk` for that withdrawal [3](#0-2) , with no check that the named operator broadcast the transaction, signed anything, or supplied the funds in the payout output.

`PayoutCheckerTask::run_once` then queries `get_first_unhandled_payout_by_operator_xonly_pk` filtered solely on the stored (attacker-controllable) pubkey matching the local operator's own key, and if found, automatically drives `handle_finalized_payout` toward a kickoff/reimbursement [4](#0-3) . This is fully automated with no human review or opt-out gate.

`is_kickoff_malicious`, the only other consumer of this attribution, merely checks that the kickoff's *claimed* operator matches the stored OP_RETURN pubkey — it never validates that the credited operator actually paid [5](#0-4) .

Equality that should hold but doesn't: `payout_payer_operator_xonly_pk (credited) == funder_of_payout_output (who actually paid)`. Because the OP_RETURN is outside the ANYONECANPAY|SINGLE commitment, any party possessing the leaked/shared user off-chain signature can construct and broadcast the payout transaction, fund the user output from their own funds, and stamp an arbitrary honest operator's pubkey into the OP_RETURN — decoupling the two sides of the equality.

### Impact Explanation
This breaks the custody binding "an operator reimbursed for a payout it never funded" (Critical per the rules). A third party (not necessarily a registered operator) can front a user's withdrawal itself and frame a chosen honest operator as the payer. That operator's own automated `PayoutCheckerTask` will then treat the withdrawal as legitimately handled by them and proceed through the kickoff/round-reimbursement machinery, draining the bridge's round funds/collateral toward reimbursing an operator for money it never actually paid out — an unauthorized/misattributed reimbursement flow that has no path back to the actual funder. This also corrupts the anti-malicious-kickoff check (`is_kickoff_malicious`), which trusts the same unauthenticated field to decide whether a kickoff by an operator is legitimate.

### Likelihood Explanation
The withdrawal off-chain signature is explicitly described as being "given to operators off-chain" (see docstring for `create_payout_txhandler`), meaning multiple parties already hold it as part of normal protocol operation, and it need not remain secret to any single operator. Constructing a payout transaction with `ANYONECANPAY|SINGLE` and an arbitrary OP_RETURN requires no privileged role — any unprivileged party with access to that off-chain signature (an operator, or anyone it was shared with/leaked to) can broadcast such a transaction to Bitcoin, a permissionless action.

### Recommendation
Bind the operator attribution to the sighash commitment: either
1. Include the OP_RETURN output in the signed digest (e.g., require the operator xonly pubkey be committed to by a signature scheme covering it, verified before accepting `payout_payer_operator_xonly_pk`), or
2. Require the crediting operator to separately co-sign or otherwise cryptographically attest (e.g., with their own collateral-tied key) that they are the fronting party for that specific withdrawal index before automation acts on it, instead of trusting raw on-chain OP_RETURN bytes.
3. At minimum, gate `PayoutCheckerTask` reimbursement automation behind an explicit local record that *this* operator itself broadcast (or authorized) the specific payout txid, rather than inferring authorization purely from parsed OP_RETURN content of an arbitrary on-chain transaction.

### Proof of Concept
1. Obtain the user's `SinglePlusAnyoneCanPay` withdrawal signature (shared off-chain with operators per protocol design, per `create_payout_txhandler` docs).
2. Construct a payout transaction spending the withdrawal UTXO with that signature, paying the user's committed output, funding fees from your own wallet, and adding your own anchor output.
3. Set the OP_RETURN output to the x-only pubkey of an arbitrary (unaware, honest) operator `X` — permitted since `ANYONECANPAY|SINGLE` does not cover this output [6](#0-5) .
4. Broadcast the transaction to Bitcoin.
5. On confirmation, `update_finalized_payouts` records `payout_payer_operator_xonly_pk = X` for that withdrawal index [7](#0-6) .
6. Operator `X`'s `PayoutCheckerTask` detects the unhandled payout matching its own key and proceeds to `handle_finalized_payout`, driving reimbursement for a withdrawal `X` never funded [8](#0-7) .

### Citations

**File:** core/src/rpc/parser/operator.rs (L181-187)
```rust
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
