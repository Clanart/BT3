### Title
Payout attribution (operator OP_RETURN) is not covered by the user's `SIGHASH_SINGLE|ANYONECANPAY` signature, letting anyone reassign or blank the reimbursement credit — ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx` that fronts a Citrea withdrawal encodes "who fronted this payout" (the operator entitled to reimbursement) in an unauthenticated `OP_RETURN` output. The only cryptographic authorization present is the user's `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` signature over the single input, which — by definition of `SIGHASH_SINGLE` — commits only to the output at the same index as the signed input (index 0, the user's payout amount). The anchor output and the `OP_RETURN` output carrying `operator_xonly_pk` are outside the signed commitment, so anyone who observes the transaction (e.g. in the mempool) can rebroadcast a mutated version with a different `OP_RETURN` value and win the race to confirm on chain.

### Finding Description
`create_payout_txhandler` builds the payout transaction with: [1](#0-0) 
- Output 0: user payout (amount signed by the user)
- Output 1: anchor for CPFP
- Output 2: `OP_RETURN` containing `operator_xonly_pk` — the identity that later claims reimbursement

The witness for input 0 is set from `user_sig` alone: [2](#0-1) 

The user's signature is required and checked to be `SinglePlusAnyoneCanPay` type, per `parse_withdrawal_sig_params` and the RPC handlers that consume it: [3](#0-2) 

`SIGHASH_SINGLE` commits only to the output whose index matches the signed input's index. With a single input at index 0, that means only output 0 (the user's payout amount/script) is covered by the signature; outputs 1 (anchor) and 2 (`OP_RETURN` with `operator_xonly_pk`) are **not** committed. Anyone possessing the raw signed transaction can therefore construct an alternate transaction spending the same input, keeping output 0 identical (so the signature remains valid), but changing/removing the `OP_RETURN` payload, and broadcast it with a higher fee to preempt the original.

Downstream, the verifier determines the reimbursement-eligible operator purely by scanning the confirmed payout transaction's `OP_RETURN`: [4](#0-3) 

That attribution is persisted as `payout_payer_operator_xonly_pk` and is later used as the sole gate for which operator may proceed with the `Reimburse` transaction flow: [5](#0-4) 

This is a direct structural analog of the reported bug: the *identity credited* for having fronted the withdrawal (`operator_xonly_pk`, analogous to `claimant`/`tx.origin` in the external report) is decoupled from the party whose signature actually authorizes the value movement (the user's `SIGHASH_SINGLE|ANYONECANPAY` signature only binds the payout amount, not who gets reimbursed for it).

### Impact Explanation
An unprivileged party who observes the broadcast/mempool-pending `payout_tx` can front-run it with a mutated version that blanks or corrupts the `OP_RETURN` output. If it confirms first:
- `parse_op_return_data`/`XOnlyPublicKey::from_slice` on the corrupted output fails, so `operator_xonly_pk` resolves to `None` in the DB (as explicitly handled in `update_finalized_payouts`): [6](#0-5) 
- With no `payer_xonly_pk` recorded, `validate_payer_is_operator` will always reject the honest operator's `get_reimbursement_txs`/reimbursement flow for that deposit (`_ => return Err(...)`): [7](#0-6) 

This satisfies the Critical-impact criterion "an honest operator permanently unable to be reimbursed," even though the operator genuinely fronted the withdrawal funds (output 0 value/script is unaffected and still pays the user correctly, since that is the only output the signature protects). No verifier, watchtower, aggregator, or operator role is required by the attacker to perform the mutation-and-race — only mempool visibility and the ability to rebroadcast with a competitive fee, both available to any unprivileged network participant.

### Likelihood Explanation
The `payout_tx` is broadcast on Bitcoin's public mempool (or otherwise becomes externally observable) before confirmation, and BIP-341 key-path spends with `SIGHASH_SINGLE|ANYONECANPAY` are well understood to leave non-committed outputs malleable to any holder of the raw transaction. No special timing beyond "see it before it confirms and outbid the fee" is needed, which is realistic given normal mempool propagation delays and operators typically not attaching maximal priority fees to a routine reimbursement-path transaction. Likelihood is Medium-to-High for a determined adversary monitoring pending payouts, though it does require winning a fee race against a re-broadcast of the original tx (mitigated somewhat if operators achieve fast confirmation via CPFP/high feerate).

### Recommendation
Bind the reimbursement-attribution data to the same signature that authorizes spending the input:
- Have the user's payout signature use `SIGHASH_ALL` (or otherwise explicitly cover the `OP_RETURN`/anchor outputs), or
- Require the operator to additionally sign (with a key already verified elsewhere in the protocol) a commitment to `operator_xonly_pk` for that specific input/outpoint, verified before any output value is released or before the payout is accepted as "finalized" for reimbursement purposes, or
- Have `update_finalized_payouts` cross-check the confirmed payout transaction's spending path/witness against an operator-specific expected value rather than trusting an unauthenticated `OP_RETURN`.

### Proof of Concept
1. Operator O calls `Withdraw`, producing a signed `payout_tx` with `output[2] = OP_RETURN(O.xonly_pk)`, using the user's `SinglePlusAnyoneCanPay` signature over `output[0]` only (`core/src/builder/transaction/operator_reimburse.rs:407-436`, `core/src/rpc/operator.rs:168-203`).
2. O broadcasts `payout_tx`; it enters the mempool.
3. Attacker A observes it, copies `input`, `witness` (signature is reusable since it doesn't commit to outputs 1/2), and `output[0]` unchanged, but strips/corrupts `output[2]`'s `OP_RETURN`.
4. A broadcasts this mutated transaction with a higher fee; it confirms first, double-spending O's original.
5. `update_finalized_payouts` parses the confirmed tx, finds no valid `operator_xonly_pk` in `OP_RETURN`, and stores `payout_payer_operator_xonly_pk = NULL` (`core/src/verifier.rs:2298-2350`).
6. O's `get_reimbursement_txs`/`validate_payer_is_operator` subsequently fails permanently for this deposit because no payer is recorded (`core/src/operator.rs:1686-1740`), even though O genuinely paid the user.

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

**File:** core/src/rpc/operator.rs (L168-203)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_withdraw(
        &self,
        request: Request<WithdrawParams>,
    ) -> Result<Response<RawSignedTx>, Status> {
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(request.into_inner())?;

        tracing::warn!("Called internal_withdraw with withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount);

        let payout_tx = self
            .operator
            .withdraw(
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
            )
            .await?;

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
    }

    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn withdraw(
        &self,
        request: Request<WithdrawParamsWithSig>,
    ) -> Result<Response<RawSignedTx>, Status> {
        tracing::info!("Withdraw rpc called");
        let params = request.into_inner();
        let withdraw_params = params.withdrawal.ok_or(Status::invalid_argument(
            "Withdrawal params not found for withdrawal",
        ))?;
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;
```

**File:** core/src/verifier.rs (L2298-2343)
```rust
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

**File:** core/src/operator.rs (L1686-1740)
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
    }
```
