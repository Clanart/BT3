## Title
Payout OP_RETURN operator attribution is unsigned and can be rewritten by any observer — ([File: core/src/builder/transaction/operator_reimburse.rs])

## Summary
`create_payout_txhandler` builds the `payout_tx` with a single Taproot key-spend input signed by the user with sighash `SinglePlusAnyoneCanPay`, and this sighash flag is enforced by `parse_withdrawal_sig_params`. Under `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`, the signature commits only to input 0 and the output at the *same index* (the user payout output); it does **not** commit to the anchor output or, critically, the `OP_RETURN` output that records which operator's x-only public key is entitled to reimbursement credit. This lets any unprivileged party observe a broadcast/pending `payout_tx`, rebuild an equivalent transaction with the same signed input/output but a different `OP_RETURN` payload naming themselves (or a colluding operator), and get it confirmed first — front-running the legitimate operator's reimbursement claim.

## Finding Description
`create_payout_txhandler` constructs the transaction as: [1](#0-0) 

The single input is spent via `SpendPath::KeySpend` with the user's `taproot::Signature`, and the parser enforces the sighash type must be `SinglePlusAnyoneCanPay`: [2](#0-1) 

`SIGHASH_SINGLE | ANYONECANPAY` only binds the signature to output index 0 (`output_txout`, the user's payout) and to the single input being spent. Outputs 1 (`anchor_output`) and 2 (the `op_return_txout` carrying `operator_xonly_pk.serialize()`) are unconstrained by the signature. This means the byte content of the `OP_RETURN` — which is later read by the operator/verifier logic to determine *which operator* fronted the withdrawal and is owed reimbursement (`handle_finalized_payout`, `get_first_unhandled_payout_by_operator_xonly_pk` in `payout_checker.rs`) — is fully attacker-malleable while the transaction remains valid and still pays the user correctly: [3](#0-2) [4](#0-3) 

The equality this breaks is: **operator credited via `OP_RETURN` xonly-pk ≠ operator whose broadcast/service actually resulted in the payout being confirmed.** Any party who sees the pending `payout_tx` (mempool or otherwise) can construct a variant with an altered `OP_RETURN` output naming a different operator xonly public key, re-sign nothing (the user signature is reused as-is since it doesn't cover that output), and get their version mined first (e.g., via a higher-fee CPFP on the still-unbound anchor output, or plain replacement in the mempool). Whoever's version confirms determines who is later credited in the reimbursement/kickoff flow, regardless of who actually serviced/paid for the withdrawal.

## Impact Explanation
This falls under "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed": the legitimate operator that processed the withdrawal request and broadcasts `payout_tx` can have their reimbursement claim hijacked by a third party who substitutes their own xonly public key into the unsigned `OP_RETURN` output and gets a competing version confirmed first. The user's payout itself is unaffected (that output is protected), so this is not a fund-theft-from-user bug, but it breaks the custody/attribution binding between "who actually fronted the withdrawal" and "who gets reimbursed," directly matching the Critical impact category for misattributed reimbursement.

## Likelihood Explanation
Any observer of the Bitcoin mempool (no privileged role required) can perform this attack purely by re-serializing the transaction with a different `OP_RETURN` payload and getting it mined ahead of/instead of the original — the exact "front-running the last-mover" pattern described in the external report, now against the OP_RETURN attribution rather than a bid price. It requires only standard mempool visibility and the ability to relay a competing transaction with sufficient fee, which is a low bar.

## Recommendation
Bind the reimbursement-attribution output to the same signature that authorizes the withdrawal, e.g., require the user to sign with `SIGHASH_ALL` (or at minimum `SIGHASH_SINGLE` extended to cover the `OP_RETURN` output — not possible natively, so use `SIGHASH_ALL`) so the operator xonly pubkey commitment cannot be altered without invalidating the signature, or otherwise commit the intended operator's identity as part of the message that is checked/verified separately from the on-chain malleable output before crediting reimbursement.

## Proof of Concept
1. Operator A prepares and broadcasts `payout_tx` (built by `create_payout_txhandler`) spending the user's withdrawal UTXO, with `OP_RETURN` = Operator A's xonly pubkey, signed by user with `SinglePlusAnyoneCanPay`.
2. Attacker observes `payout_tx` in the mempool, copies the same input/witness and output 0 (user payout, protected by signature), but replaces output 2's `OP_RETURN` payload with Operator B's (or their own operator's) xonly pubkey; optionally bumps fee via the anchor output.
3. Attacker broadcasts the modified transaction; since the signature (`SinglePlusAnyoneCanPay`) does not cover output 2, it remains valid and can be relayed/mined in place of, or instead of, Operator A's original.
4. `PayoutCheckerTask::run_once` and `Operator::handle_finalized_payout` read the confirmed transaction's `OP_RETURN` operator pubkey to determine who is credited, crediting Operator B despite Operator A being the one who serviced the withdrawal. [5](#0-4)

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

**File:** core/src/task/payout_checker.rs (L39-52)
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
