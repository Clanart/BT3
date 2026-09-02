### Title
Payout tx malleability via `SinglePlusAnyoneCanPay` lets any withdrawer strip the OP_RETURN and orphan the fronting operator's reimbursement — ([File: core/src/verifier.rs])

### Summary
The user's withdrawal signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to input 0's prevout and output 0. Anyone who observes this signature in the mempool (including the withdrawer themselves, who is an unprivileged attacker) can rebuild the payout transaction with arbitrary other inputs/outputs — in particular, dropping the OP_RETURN output that records the fronting operator's x-only pubkey — and race it to confirmation ahead of the operator's own broadcast. `update_finalized_payouts` then records `operator_xonly_pk = NULL` for that withdrawal, permanently severing the operator's only lookup path back to its payout.

### Finding Description
Binding claimed: `operator_xonly_pk` recorded for withdrawal idx `i` == the x-only pubkey of the party who funded output 0 of the mined payout tx.

This binding is **broken** because the on-chain payout transaction's OP_RETURN output (index 2) is not covered by the user's signature at all, so the mined transaction that "wins" the race for the withdrawal outpoint does not have to be the operator's transaction, and even if it descends from it, its OP_RETURN can be entirely dropped.

Trace:
1. `parse_withdrawal_sig_params` enforces the user's `input_signature.sighash_type == TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) .
2. `create_payout_txhandler` builds the payout tx as `[input: withdrawal UTXO (KeySpend)] -> [output0: user payout, output1: anchor, output2: OP_RETURN(operator_xonly_pk)]` and installs the user's key-spend witness only on input 0 [2](#0-1) .
3. `calculate_pubkey_spend_sighash` computes the sighash using `Prevouts::One(txin_index, prevout)` for `SinglePlusAnyoneCanPay`, meaning the signature is only bound to input 0's own prevout data and, per SIGHASH_SINGLE, output 0 [3](#0-2) . The operator verifies this exact signature against the same sighash before broadcasting [4](#0-3) .
4. Because outputs 1 and 2 (anchor, OP_RETURN) and any other inputs are unsigned, an attacker who has seen the witness (e.g. the withdrawer themselves, once the operator's tx enters the public mempool) can construct via `TxHandlerBuilder` a new transaction reusing input 0 with its witness verbatim and output 0 verbatim, but omitting the OP_RETURN output (and optionally the anchor), adding their own fee-paying input/output. This new transaction is consensus-valid and conflicts with (double-spends) the operator's original broadcast.
5. If the attacker's variant confirms first, `get_payout_txs_for_withdrawal_utxos` picks up whichever tx spent the withdrawal outpoint, keyed purely by outpoint, not by the operator's intended txid [5](#0-4) .
6. `update_finalized_payouts` then calls `get_first_op_return_output` on the mined tx, finds none, and sets `operator_xonly_pk = None`, writing `NULL` to `payout_payer_operator_xonly_pk` [6](#0-5) . `get_first_op_return_output` itself simply scans for the first OP_RETURN output and returns `None` if absent [7](#0-6) .
7. With `payout_payer_operator_xonly_pk = NULL`, `get_first_unhandled_payout_by_operator_xonly_pk` will never return this withdrawal for the honest operator's key [8](#0-7) , and `is_kickoff_malicious` explicitly treats a missing operator xonly pk as malicious [9](#0-8) , so if the operator later sends its kickoff to claim reimbursement, verifiers flag it as malicious.

None of the existing guards catch this: `Verifier::is_deposit_valid` and `SPV::verify` only check that the payout tx is confirmed and spends the correct withdrawal outpoint/index — they never check who funded output 0 versus the OP_RETURN content, and the sighash mechanism itself is what leaves outputs 1/2 unauthenticated.

### Impact Explanation
The honest operator that fronted the withdrawal loses its only DB-recorded linkage (`payout_payer_operator_xonly_pk`) between the withdrawal and its own x-only pubkey. Consequences:
- `get_first_unhandled_payout_by_operator_xonly_pk` never surfaces this payout for the operator, so the operator cannot proceed with `handle_finalized_payout`/kickoff flow for this withdrawal.
- If the operator nonetheless sends a kickoff for this deposit, `is_kickoff_malicious` classifies it as malicious, exposing the operator's collateral to being challenged/burned even though it genuinely paid the user.
- The BTC the operator fronted is unrecoverable via the intended reimbursement path — the collateral parked in the round tx for that kickoff slot becomes reimbursement-dead.

This matches the Critical categories "an honest operator permanently unable to be reimbursed" / "an honest operator's collateral burned." It is repeatable per withdrawal/operator: any withdrawer can perform this against whichever operator fronts their payout, and it does not require compromising any key, majority hashrate, or privileged role — only normal fee-paying transaction broadcast capability.

### Likelihood Explanation
Preconditions: attacker must be the party requesting a withdrawal (or otherwise obtain the broadcasted signature from the public mempool, which is trivial since it's their own signature as recipient of the payout), and must get a stripped variant of the payout tx mined before the operator's original confirms. This only requires normal Bitcoin fee-bidding (pay a higher fee / get to a miner faster), no privileged access, no majority hashrate. The cost is one extra small-fee Bitcoin transaction. This is realistically executable by any user with a Bitcoin wallet and is repeatable across every withdrawal/operator pairing.

### Recommendation
Do not rely on an unauthenticated OP_RETURN output to bind the fronting operator. Either:
- Require the user's payout signature to cover the OP_RETURN output too (e.g. sign with `AllPlusAnyoneCanPay` or `Default` instead of `SinglePlusAnyoneCanPay`, or otherwise commit output 1/2 into the sighash), so the operator's xonly-pk output cannot be malleated away without invalidating the signature, or
- Bind the operator identity via

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

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
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

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
    }
```

**File:** core/src/database/verifier.rs (L282-313)
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

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L2311-2342)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```
