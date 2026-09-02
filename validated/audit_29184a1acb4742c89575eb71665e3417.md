### Title
Payout attribution (operator-credited-as-payer) is not covered by the user's withdrawal signature, allowing theft of reimbursement credit for a withdrawal another party actually fronted - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The `payout_tx` that fronts a Citrea withdrawal is only partially signed by the user: the signature uses `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`, which binds only the withdrawal input and the single user-payout output. The transaction's third output — an `OP_RETURN` that records which operator's x-only pubkey fronted the payout, and which is later read off-chain to decide who gets reimbursed the full `bridge_amount` — is **not** covered by that signature. Any party who obtains the (broadcast or off-chain-shared) user signature can rebuild a competing `payout_tx` with the identical, signature-satisfying user output, but with an arbitrary `operator_xonly_pk` written into the `OP_RETURN`, and get it confirmed instead of the honest fronting operator's transaction. Reimbursement bookkeeping (`update_finalized_payouts` / `validate_payer_is_operator`) trusts whichever OP_RETURN ends up on-chain, so the attacker's identity is credited as the payer and can later claim the `Reimburse` transaction for a withdrawal it never funded.

### Finding Description
`create_payout_txhandler` builds the payout transaction with:
1. Input: the withdrawal UTXO, spent with `SpendPath::KeySpend` and the user's `taproot::Signature`.
2. Output 0: the user's payout.
3. Output 1: CPFP anchor.
4. Output 2: `OP_RETURN` containing the fronting operator's x-only pubkey. [1](#0-0) 

The user signature is explicitly required to be `SinglePlusAnyoneCanPay`, as documented in the verification error in `Operator::withdraw`: [2](#0-1) 

`SIGHASH_SINGLE | ANYONECANPAY` only commits to the spent input and the output at the same index as that input (index 0, the user payout). It does **not** commit to the anchor output or, critically, to the `OP_RETURN` output holding `operator_xonly_pk`. This means the operator-attribution field is fully malleable by anyone holding the user's signature: they can build their own version of the same transaction, keep output 0 identical (so the signature still verifies), and set an arbitrary `operator_xonly_pk` in the `OP_RETURN`.

Downstream, the chain-observed `OP_RETURN` is the sole source of truth for "who fronted this withdrawal":
- `update_finalized_payouts` parses whichever transaction actually spent the withdrawal UTXO on-chain and extracts `operator_xonly_pk` purely from its `OP_RETURN`, storing it as `payout_payer_operator_xonly_pk`. [3](#0-2) 
- `PayoutCheckerTask` and `Operator::validate_payer_is_operator` later use this stored value to decide which operator is eligible to receive the kickoff/reimbursement flow for that deposit, with no additional check that the recorded pubkey belongs to the party that actually built/paid-for/broadcast the transaction. [4](#0-3) [5](#0-4) 

Because operators legitimately race each other to front the same withdrawal (the same off-chain signature is handed to multiple operators, as seen in the concurrent-withdrawal test issuing the same `withdraw_params` to `operator0` and `operator1`), a malicious participant already has legitimate access to a valid signature for the withdrawal input. Instead of constructing its own honest payout, it can watch the mempool for a competing operator's `payout_tx`, extract the reusable signature, and rebroadcast a fee-bumped clone with its own `operator_xonly_pk` in the `OP_RETURN`, since bitcoin's mempool/consensus rules only allow one version of the double-spending input to confirm. [6](#0-5) 

This breaks the bridge-custody binding "the operator credited versus the party that paid": the party that ends up being reimbursed `bridge_amount` from the move-to-vault UTXO is not cryptographically bound to the party whose funds/broadcast actually satisfied the user's withdrawal.

### Impact Explanation
Whoever wins this race is credited as the payer in the `withdrawals` table and can subsequently walk through `get_reimbursement_txs` / `handle_finalized_payout` to claim the `Reimburse` transaction, receiving the entire `bridge_amount` from the deposit's move-to-vault UTXO — money that was never actually fronted from that party's own capital in excess of ordinary Bitcoin transaction fees. Conversely, the operator who genuinely intended to front the withdrawal (and may have already committed to doing so) is locked out of reimbursement for that deposit, since `validate_payer_is_operator` will reject their own reimbursement attempt once a different `payer_xonly_pk` is recorded. This matches the Critical impact "an operator reimbursed for a payout it never funded" combined with "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
The attack only requires observing a broadcast (but unconfirmed) `payout_tx` in the mempool or otherwise obtaining a copy of the withdrawal's `SinglePlusAnyoneCanPay` signature (which by design is shared with multiple operators for the same withdrawal), and rebuilding/fee-bumping a variant transaction — a purely mechanical, low-cost operation requiring no special access to protocol secrets, verifier keys, or aggregator infrastructure. The main constraint is that fully cashing out the stolen credit still requires the attacker to operate an already-configured operator identity (collateral, round/kickoff infra) — but this is squarely the adversarial-operator threat model this bridge protects against, matching the explicitly listed critical impact category.

### Recommendation
Bind the `operator_xonly_pk` (and ideally the anchor output) into the signed message the user commits to for the withdrawal, e.g. by having the user sign with `SIGHASH_ALL` (or a custom sighash covering all outputs) instead of `SIGHASH_SINGLE | ANYONECANPAY`, or by moving the payer-attribution to a mechanism validated by the N-of-N/verifier set rather than an unauthenticated on-chain `OP_RETURN` that any holder of the reusable signature can rewrite.

### Proof of Concept
1. User signs a withdrawal with `SIGHASH_SINGLE | ANYONECANPAY` and shares it off-chain with operators A and B (both entitled to attempt the payout), per `Operator::withdraw`.
2. Operator A broadcasts `payout_tx_A` = {input: withdrawal UTXO (sig), out0: user payout, out1: anchor, out2: OP_RETURN(A_xonly_pk)}.
3. Operator B observes `payout_tx_A` in the mempool, extracts the signature (valid for out0), and constructs `payout_tx_B` = {same input/sig, same out0, own anchor funding, out2: OP_RETURN(B_xonly_pk)}, then broadcasts it with a higher fee.
4. `payout_tx_B` confirms instead of `payout_tx_A` (same input, so they conflict; only one can be mined).
5. `update_finalized_payouts` records B as `payout_payer_operator_xonly_pk` for this withdrawal purely from the confirmed OP_RETURN.
6. B runs `get_reimbursement_txs` / the kickoff flow and is reimbursed the full `bridge_amount`, while A — who genuinely intended to front the withdrawal — is rejected by `validate_payer_is_operator` since the recorded payer no longer matches A's key.

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

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L1686-1739)
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

        tracing::info!(
            "Payer xonly pk, payout blockhash and kickoff txid found and valid for own operator for the requested deposit id: {}, payer xonly pk: {:?}, payout blockhash: {:?}, kickoff txid: {:?}",
            deposit_id,
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid
        );

        Ok((payout_blockhash, kickoff_txid))
```

**File:** core/src/verifier.rs (L2283-2343)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();

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

**File:** core/src/test/deposit_and_withdraw_e2e.rs (L2118-2141)
```rust
                    let withdraw_params = WithdrawParams {
                        withdrawal_id: i as u32,
                        input_signature: sigs[i].serialize().to_vec(),
                        input_outpoint: Some(withdrawal_utxos[i].into()),
                        output_script_pubkey: payout_txouts[i].script_pubkey.to_bytes(),
                        output_amount: payout_txouts[i].value.to_sat(),
                    };
                    let verification_signature = sign_withdrawal_verification_signature::<
                        OperatorWithdrawalMessage,
                    >(
                        &config, withdraw_params.clone()
                    );

                    let verification_signature_str = verification_signature.to_string();

                    withdrawal_requests.push(operator0.withdraw(WithdrawParamsWithSig {
                        withdrawal: Some(withdraw_params.clone()),
                        verification_signature: Some(verification_signature_str.clone()),
                    }));

                    withdrawal_requests.push(operator1.withdraw(WithdrawParamsWithSig {
                        withdrawal: Some(withdraw_params.clone()),
                        verification_signature: Some(verification_signature_str.clone()),
                    }));
```
