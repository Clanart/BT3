### Title
Payout attribution can be hijacked by any mempool observer via SIGHASH_SINGLE|ANYONECANPAY, permanently freezing the vault and denying the honest operator reimbursement - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The `payout_tx` that fronts a Citrea withdrawal is signed by the user with `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to the single input and its same-index output. The second (anchor) and third (`OP_RETURN` operator-attribution) outputs are **not** covered by the signature. Any party who observes the broadcast (unconfirmed) `payout_tx` can rebroadcast a variant with the identical signed input/output but a different, self-chosen `OP_RETURN` xonly-pubkey and win the confirmation race, stealing the "who fronted this withdrawal" attribution that the reimbursement pipeline relies on.

### Finding Description
`create_payout_txhandler` builds the payout transaction with a `KeySpend` input that is signed once via `set_p2tr_key_spend_witness(&user_sig, 0)`, and the `WithdrawParams.input_signature` is explicitly documented and enforced to be `SinglePlusAnyoneCanPay`: [1](#0-0) [2](#0-1) 

`SIGHASH_SINGLE | ANYONECANPAY` commits only to input 0 and the output at the same index (output 0, the user-payout output). It leaves the CPFP anchor output and the `OP_RETURN` output — which encodes `operator_xonly_pk`, the value later used to attribute who fronted the payout — completely unconstrained: [3](#0-2) 

Downstream, `update_finalized_payouts` parses whatever 32 bytes appear in the confirmed transaction's `OP_RETURN` and stores it as the payer, with no check that it corresponds to a registered/known operator: [4](#0-3) 

`validate_payer_is_operator` and `get_first_unhandled_payout_by_operator_xonly_pk` then key entirely off this stored xonly-pubkey to decide who is allowed to run the reimbursement (kickoff) flow: [5](#0-4) [6](#0-5) 

Because the attribution output is unsigned, an unprivileged attacker (no operator, verifier, or key role required) can:
1. Monitor the Bitcoin mempool for an honest operator's `payout_tx` (this operator has already fronted the user's withdrawal with their own funds via the `withdraw` RPC flow: `core/src/operator.rs:560-627`).
2. Reuse the exact same `input_utxo`/`user_sig` witness (the SINGLE|ANYONECANPAY signature is valid for any transaction that keeps output 0 identical) and construct a competing transaction with an `OP_RETURN` containing an arbitrary xonly-pubkey the attacker controls (or garbage that still parses as a valid xonly key).
3. Get this variant confirmed first (V3/TRUC transactions are inherently RBF-replaceable, and fee-bumping only requires spending the anyone-can-spend anchor output, which requires no signature over the protected part of the transaction).

### Impact Explanation
Once the attacker's variant confirms:
- The user is still correctly paid (output 0 is signature-committed), so no direct fund loss to the user occurs.
- The `withdrawals` DB record's `payout_payer_operator_xonly_pk` is now the attacker's arbitrary pubkey, not the honest operator's `self.signer.xonly_public_key`.
- No real operator's `get_first_unhandled_payout_by_operator_xonly_pk` query or `validate_payer_is_operator` check will ever match this fake pubkey, so no operator can ever call `handle_finalized_payout` / progress the kickoff/reimburse flow for this deposit.
- The honest operator who already fronted the withdrawal out of pocket is permanently unable to be reimbursed, and the corresponding `move_to_vault` UTXO can never be spent by the legitimate `reimburse_tx` path, effectively freezing that vault UTXO forever.

This matches two of the explicitly listed Critical impacts: "an honest operator permanently unable to be reimbursed" and "a vault UTXO permanently frozen," triggered purely by an unauthenticated party broadcasting a transaction — no operator/verifier/aggregator role, key compromise, or majority hashrate required.

### Likelihood Explanation
Likelihood is moderate-to-high in an adversarial mempool environment: the attack requires only mempool visibility and the ability to broadcast a Bitcoin transaction (no special access to Clementine's gRPC surface, no operator credentials). The transaction is built as `NON_STANDARD_V3` with a zero-value CPFP anchor, meaning fee-bumping/replacement is cheap and standard, and RBF/TRUC replacement rules make racing straightforward. The main uncertainty is timing (the attacker must act before the honest operator's original tx confirms), which is realistic given normal Bitcoin block intervals and mempool propagation delay.

### Recommendation
Change the withdrawal signature's sighash type (or the transaction structure) so that the `OP_RETURN` attribution output (and ideally the anchor output) is covered by the signature — e.g., use `SIGHASH_ALL` combined with a separate mechanism for fee-bumping that doesn't require mutable outputs, or have each operator generate and use their own distinct commitment/signature per-operator so no other party can reuse the witness with a different attribution. Alternatively, validate on the verifier/operator side that `operator_xonly_pk` in a confirmed payout's `OP_RETURN` belongs to the actual current operator set that the aggregator dispatched the withdrawal request to, and treat unknown/unregistered attribution as requiring manual recovery rather than silently orphaning the deposit.

### Proof of Concept
1. Operator A receives a `withdraw` RPC call and broadcasts `payout_tx` (input signed with `SinglePlusAnyoneCanPay`, `OP_RETURN` = Operator A's xonly-pubkey), fronting the user's BTC.
2. Before `payout_tx` confirms, an attacker (no special role) observes it in the mempool, and using `create_payout_txhandler` logic (or by hand) constructs `payout_tx'` with:
   - Same input (`input_utxo`), same `user_sig` witness, same output 0 (paying the user identically).
   - A different `OP_RETURN` output containing the attacker's own generated xonly-pubkey (or arbitrary 32 bytes that parse as a valid xonly key).
3. Attacker broadcasts `payout_tx'` with a higher-fee CPFP child spending the anchor output (no signature needed on the anchor), causing `payout_tx'` to be mined instead of Operator A's `payout_tx`.
4. `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) records the attacker's fake xonly-pubkey as the payer for this withdrawal.
5. `get_first_unhandled_payout_by_operator_xonly_pk` / `validate_payer_is_operator` never match any real operator's key for this deposit going forward, so Operator A can never reimburse itself, and the deposit's `move_to_vault` UTXO remains permanently unspent.

Note: The exact provenance/ownership model of the `input_utxo` funding the user's payout was not fully traceable within index limits (it is referred to as the "user's withdrawal input" in comments, yet is fetched by outpoint parameters supplied to the `withdraw` RPC by the caller); this does not affect the core finding, since the vulnerability hinges purely on the unsigned `OP_RETURN` attribution output, but a full review of `core/src/operator.rs` (`withdraw` function) and how `in_outpoint` is originally selected/funded is recommended to confirm there is no additional mitigating constraint not visible in the indexed snippets.

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

**File:** core/src/database/verifier.rs (L282-298)
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
```
