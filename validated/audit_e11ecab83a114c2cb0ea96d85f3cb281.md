Confirmed: the user signature uses `SinglePlusAnyoneCanPay` sighash type, which commits only to the withdrawal input and the single payout output — not to the OP_RETURN output that carries the "fronting operator" attribution [1](#0-0) . This is the mechanism intended to let any operator race to front a given withdrawal by appending their own OP_RETURN with their own `operator_xonly_pk` [2](#0-1) . Because the signature doesn't cover this output, whoever *broadcasts* a valid payout transaction controls what pubkey appears in the OP_RETURN, independent of whose UTXO actually funds the payment.

### Title
Payer attribution derived from unauthenticated OP_RETURN allows an operator to be credited for a peg-out it never funded - (File: `circuits-lib/src/bridge_circuit/mod.rs`, `core/src/verifier.rs`)

### Summary
The bridge determines which operator gets reimbursed for a payout purely by parsing the *first* OP_RETURN output of the broadcast payout transaction and reading a raw 32-byte value as an x-only public key [3](#0-2) . This value is never bound by the user's signature (`SinglePlusAnyoneCanPay` only covers the withdrawal input and the fixed payout output) and is never checked against who actually supplied the transaction's funding input. Anyone able to construct and broadcast a valid payout transaction for the withdrawal (which, by protocol design, is any operator, since the race-to-front model deliberately leaves the OP_RETURN unsigned) can insert an *arbitrary* operator's `xonly_pk` in that OP_RETURN — including one belonging to an operator who did not fund the transaction. This directly breaks the "operator credited versus the party that paid" custody binding required by the protocol.

### Finding Description
The protocol's payout flow is: a user pre-signs a `SinglePlusAnyoneCanPay` signature over the withdrawal input and payout output only, allowing whichever operator wants to front the withdrawal to add their own inputs/fee and their own OP_RETURN commitment, then broadcast [4](#0-3) [5](#0-4) .

Once broadcast, the verifier's block-sync logic determines "who paid" solely by parsing the OP_RETURN of the mined payout transaction: [6](#0-5) 

This `operator_xonly_pk` is persisted as the "payer" of the withdrawal [7](#0-6)  and is later used both to gate an operator's own reimbursement flow (`validate_payer_is_operator`) [8](#0-7)  and to determine whether a kickoff is malicious by comparing the recorded payer to the kickoff sender's xonly_pk [9](#0-8) . The same unauthenticated parsing feeds the on-chain-verified bridge circuit's `deposit_constant`, which is what the Groth16 proof and disprove path bind to [10](#0-9) .

At no point is the OP_RETURN's `operator_xonly_pk` checked against a signature from that operator, against the source of the transaction's funding inputs, or against any other cryptographic proof that this operator actually paid. The only two checks performed are (a) that some OP_RETURN exists (`get_first_op_return_output`, which just returns the first OP_RETURN output found, with no uniqueness/position/format tag validation — directly analogous to the "bh=" tag-boundary leniency in the external report) and (b) that its content parses as 32 bytes / a valid x-only public key. This is exactly the class of leniency described in the external report: a value ("bh="/here, the fronting-operator commitment) is trusted based on a loosely verified positional heuristic rather than being cryptographically bound to the party it is meant to represent.

### Impact Explanation
An operator (call them B) who does not fund a withdrawal can still be recorded in the database as the "payer" of that withdrawal simply by getting *any* valid payout transaction mined that includes B's `xonly_pk` in an OP_RETURN, while the honest operator A actually supplies the funding UTXO(s)/fee for the transaction. Concretely: A constructs and would normally sign/broadcast the payout tx with A's own OP_RETURN; but because the user's `SinglePlusAnyoneCanPay` signature does not cover the OP_RETURN, any party capable of assembling and rebroadcasting a competing, still-validly-signed version of the payout transaction (adding their own funding inputs, fee, and OP_RETURN with B's pubkey) can win the race instead. Once mined, `update_finalized_payouts` records B as payer regardless of who actually paid [11](#0-10) . B can then successfully pass `validate_payer_is_operator` and `is_kickoff_malicious`, and proceed through kickoff/reimbursement to be credited for a peg-out it never funded, while A — who may have paid the sats to the user — has no path to reimbursement. This matches the Critical impact category: "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
The likelihood is bounded by mempool/broadcast dynamics: the attacker must get their version of the payout transaction (spending the same withdrawal UTXO, satisfying the user's `SinglePlusAnyoneCanPay` signature on the fixed output) confirmed instead of the honest operator's version. Since `SinglePlusAnyoneCanPay` explicitly permits arbitrary additional inputs/outputs to be appended by anyone holding the signature, and withdrawal signatures given by users to operators are not restricted to a single designated operator in the on-chain enforcement (the OP_RETURN, the only place operator identity is recorded, is unsigned), any operator (or a party who intercepts/monitors mempool broadcasts of the honest operator's payout tx and RBFs/out-competes it) can perform this attack. This does not require any privileged role beyond being a participating operator, and no verifier/aggregator/security-council collusion is needed.

### Recommendation
Cryptographically bind the OP_RETURN's `operator_xonly_pk` to the actual funder of the payout transaction, e.g., by requiring the operator's kickoff-time signature to prove they supplied the specific input(s) that funded the payout, or by extending the sighash coverage (e.g., use a covenant/pre-commitment scheme) so the winning operator's identity cannot be substituted post-signing. At minimum, `update_finalized_payouts` and the bridge circuit should verify that the funding input(s) of the payout transaction are provably controlled by the `xonly_pk` claimed in the OP_RETURN (e.g., checking that at least one non-withdrawal input's `previous_output` script pubkey commits to that same key, or requires a matching signature), rather than trusting an unauthenticated OP_RETURN value. Additionally, `get_first_op_return_output`/`parse_op_return_data` should enforce that exactly one OP_RETURN exists and is at a canonical, expected output position, to close the "loosely verified tag position" gap analogous to the "bh=" issue.

### Proof of Concept
1. User signs a `SinglePlusAnyoneCanPay` withdrawal signature over the fixed withdrawal input and payout output (per `parse_withdrawal_sig_params`) [1](#0-0) .
2. Honest operator A constructs and broadcasts payout_tx_A: input = withdrawal UTXO + A's funding input, output[0] = user payout, output[1] = anchor, output[2] = OP_RETURN(A_xonly_pk) [2](#0-1) .
3. Before payout_tx_A confirms, attacker/operator B constructs payout_tx_B spending the *same* withdrawal UTXO with the same signature (valid because `SinglePlusAnyoneCanPay` allows any additional inputs), using B's own funding input for the fee, output[0] identical (user payout, signature-bound), output[2] = OP_RETURN(B_xonly_pk).
4. B gets payout_tx_B mined instead (e.g., via higher fee/RBF).
5. `update_finalized_payouts` parses the OP_RETURN of the mined transaction and records B's `xonly_pk` as the payer [12](#0-11) , even though B did not supply the user-payout funds (A's committed funding input was displaced/unused).
6. B subsequently passes `validate_payer_is_operator` [13](#0-12)  and `is_kickoff_malicious`'s payer-match check [14](#0-13) , and can proceed through kickoff and reimbursement, being credited as though it had fronted the withdrawal.

### Citations

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

**File:** core/src/builder/transaction/operator_reimburse.rs (L387-436)
```rust
/// Creates a [`TxHandler`] for the `payout_tx`.
///
/// This transaction is sent by the operator to front a peg-out, after which operator will send a kickoff transaction to get reimbursed.
///
/// # Inputs
/// 1. UTXO: User's withdrawal input (committed in Citrea side, with the signature given to operators off-chain)
///
/// # Outputs
/// 1. User payout output
/// 2. OP_RETURN output (with operators x-only pubkey that fronts the peg-out)
///
/// # Arguments
/// * `input_utxo` - The input UTXO for the payout, committed in Citrea side, with the signature given to operators off-chain.
/// * `output_txout` - The output TxOut for the user payout.
/// * `operator_xonly_pk` - The operator's x-only public key that fronts the peg-out.
/// * `user_sig` - The user's signature for the payout, given to operators off-chain.
/// * `network` - The Bitcoin network.
///
/// # Returns
/// A [`TxHandler`] for the payout transaction, or a [`BridgeError`] if construction fails.
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L608-617)
```rust
/// Parses the OP_RETURN data from a Bitcoin script. It retrieves the first data push after an OP_RETURN.
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
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

**File:** core/src/verifier.rs (L2283-2352)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;

        Ok(())
```

**File:** core/src/database/verifier.rs (L198-251)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;

        Ok(())
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
