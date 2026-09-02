This confirms the vulnerability path clearly. `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) purely joins on `bitcoin_syncer_spent_utxos.txid/vout` matching `withdrawals.withdrawal_utxo_txid/vout` — i.e., it identifies the payout tx solely as whichever transaction is found on-chain spending the withdrawal outpoint, with no check that it matches a specific txid the operator broadcast. Combined with the fact that the user's signature uses `TapSighashType::SinglePlusAnyoneCanPay` (enforced in `core/src/rpc/parser/operator.rs:182-187`, consumed in `core/src/operator.rs:630-637`), which per BIP341 only commits to input 0 and output 0 — outputs 1 (anchor) and 2 (OP_RETURN with operator pubkey, set in `create_payout_txhandler` at `core/src/builder/transaction/operator_reimburse.rs:407-436`) are unsigned and freely rewritable by anyone who observes the signed input. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Payout tx OP_RETURN operator pubkey unsigned under SinglePlusAnyoneCanPay allows credit-hijacking of another operator's fronted withdrawal - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The Payout transaction created by `create_payout_txhandler` places the operator's x-only pubkey in an unsigned OP_RETURN output (index 2), while the user's `SinglePlusAnyoneCanPay` signature only commits to input 0 and output 0 per BIP341. Because `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` binds `payout_payer_operator_xonly_pk` to whichever transaction is found on-chain spending the withdrawal outpoint (parsed from that unsigned OP_RETURN), an attacker who observes operator A's signed payout input can rebroadcast a modified transaction naming operator B in the OP_RETURN and get B credited instead of A.

### Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk` for withdrawal `i` (as read by `Verifier::is_kickoff_malicious` at [5](#0-4)  and by `get_first_unhandled_payout_by_operator_xonly_pk` at [6](#0-5) ) should equal the operator whose 10 BTC actually funded output 0 of the mined payout tx. In reality this value equals `parse_op_return_data(op_return_output.script_pubkey)` of whichever transaction spends `withdrawal_utxo_txid`/`vout` on-chain, as computed in `update_finalized_payouts` [7](#0-6) , and that OP_RETURN byte range is never covered by the user's `SinglePlusAnyoneCanPay` signature verified in `Operator::withdraw` [8](#0-7) .

Root cause: `TapSighashType::SinglePlusAnyoneCanPay` (BIP341 SIGHASH_SINGLE|ANYONECANPAY) commits only to the input being spent and the output at the same index (index 0 = user's payout amount/script). Outputs 1 (anchor) and 2 (OP_RETURN with `operator_xonly_pk.serialize()`, built at [9](#0-8) ) are entirely unauthenticated, and ANYONECANPAY additionally permits adding extra inputs. Once operator A funds the payout tx and it appears in the mempool (or is even just constructed and shared, since the witness itself — signature + pubkey — is visible to anyone who can see the transaction), any unprivileged party can:
1. Copy input 0 (withdrawal outpoint) and its witness (the valid `SinglePlusAnyoneCanPay` Schnorr signature) unchanged.
2. Keep output 0 (amount/script_pubkey) unchanged, satisfying the SIGHASH_SINGLE commitment.
3. Replace output 2's OP_RETURN payload with operator B's serialized x-only pubkey (any other real registered operator).
4. Add their own input(s) (permitted by ANYONECANPAY) to cover fees and replace/outrace A's original broadcast (e.g., via higher feerate), getting the attacker's version mined instead.

`update_finalized_payouts` then records `payout_payer_operator_xonly_pk = B` for this withdrawal, purely from the on-chain OP_RETURN, with no cross-check against which operator's wallet actually funded output 0. Existing guards do not catch this: `Operator::withdraw`'s signature check only validates output 0 and input 0 [8](#0-7) ; `Verifier::is_kickoff_malicious` only compares the OP_RETURN-derived pubkey against the kickoff's claimed `operator_xonly_pk`, which will match B trivially since B's own kickoff will name B [10](#0-9) ; `SPV::verify` only proves the payout tx is in a valid block, not who funded it.

### Impact Explanation
Operator B's `PayoutCheckerTask::run_once` [11](#0-10)  will pick up the hijacked withdrawal via `get_first_unhandled_payout_by_operator_xonly_pk` (filtered on B's own key) and call `handle_finalized_payout`, ultimately allowing B to kick off the Reimburse flow and claim funds for a payout it never funded — a direct "operator reimbursed for a payout it never funded" (Critical). Simultaneously, operator A, who genuinely fronted the 10 BTC in output 0, can never be matched to this withdrawal (the withdrawal's `payout_payer_operator_xonly_pk` is now B, not A), so A's kickoff for this deposit will fail `is_kickoff_malicious`'s pubkey match and be flagged malicious, permanently blocking A's legitimate reimbursement and risking collateral loss. This is repeatable per-withdrawal and requires no special access — any observer of the payout's signed input/mempool entry can perform it against any operator, for any deposit.

### Likelihood Explanation
The precondition is simply that an honest operator front a withdrawal using the standard `withdraw` RPC flow, which always signs with `SinglePlusAnyoneCanPay`, and that the attacker sees the transaction (mempool visibility) before/while it confirms. The attacker only needs to pay the marginal Bitcoin transaction fee to get their modified version mined first (e.g., via a competing fee-bumped conflicting spend of the same input using ANYONECANPAY to add fee inputs), which is cheap and fully within the stated attacker capabilities (broadcast transactions, choose scripts/signatures/witnesses). No verifier, operator, or aggregator compromise is needed.

### Recommendation
Commit the operator identity into the signed part of the Payout transaction, e.g., have the user's signature cover all outputs (use `TapSighashType::Default`/`All` instead of `SinglePlusAnyoneCanPay`, or otherwise commit to a hash of the OP_RETURN operator pubkey within output 0's script/amount encoding), or otherwise bind `payout_payer_operator_xonly_pk` to a value that only the actual funding operator can produce (e.g., require the operator to additionally sign/co-sign the full transaction with its own key over all outputs, and validate that signature server-side before recording payer credit).

### Proof of Concept
```rust
// cargo test (regtest, no mainnet/live Citrea) sketch:
// 1. Set up two operators A and B with known xonly pubkeys via existing test harness (create_actors).
// 2. Run a normal deposit + withdrawal flow where operator A calls `withdraw` (core/src/operator.rs withdraw),
//    producing a fully signed Payout tx with SinglePlusAnyoneCanPay witness on input 0, output0=user payout,
//    output2 OP_RETURN = A.serialize().
// 3. Before/while A's tx is only in mempool, attacker constructs a new Transaction:
//    - input0 = same outpoint + copied witness from A's tx (unchanged)
//    - output0 = identical to A's tx (preserve SIGHASH_SINGLE binding)
//    - output2 = OP_RETURN with B.serialize() instead of A.serialize()
//    - add attacker-funded extra input(s) for fees (ANYONECANPAY allows this)
// 4. Broadcast attacker's tx with higher feerate so it confirms (mine block).
// 5. Run bitcoin syncer / verifier's update_finalized_payouts for that block.
// 6. Assert: db.get_payout_info_from_move_txid(...) for this withdrawal returns operator_xonly_pk == Some(B).
// 7. Run PayoutCheckerTask::run_once for operator B's node/db; assert it returns Ok(true) and
//    calls handle_finalized_payout for a deposit B never funded.
// 8. Separately assert operator A's kickoff for the same deposit now fails Verifier::is_kickoff_malicious
//    (returns Ok(true) i.e. "malicious") even though A genuinely paid output 0.
```

### Citations

**File:** core/src/database/verifier.rs (L170-196)
```rust
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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
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

**File:** core/src/verifier.rs (L1871-1890)
```rust
        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
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

**File:** core/src/task/payout_checker.rs (L39-66)
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
```
