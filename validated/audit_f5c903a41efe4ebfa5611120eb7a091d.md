## Analysis Summary

The binding claimed to be broken: **the party credited for withdrawal index `i`** (`withdrawals.payout_payer_operator_xonly_pk` in the DB, derived from the OP_RETURN of whatever transaction the chain shows spending `withdrawal_utxo_txid:withdrawal_utxo_vout`) **must equal the party whose funds actually paid output 0 of that withdrawal**.

Tracing the code confirms this equality is not enforced, and is trivially breakable by the withdrawer themselves:

- `create_payout_txhandler` [1](#0-0)  builds input 0 as a **key-spend** input, and the user's signature is required to use `TapSighashType::SinglePlusAnyoneCanPay`, enforced both operator-side (`Operator::withdraw`) [2](#0-1)  and in the shared parser [3](#0-2) .
- `SinglePlusAnyoneCanPay` only commits to input 0's own prevout and output 0 — it leaves every other input and output, including the anchor and the OP_RETURN (which encodes `operator_xonly_pk`), completely unsigned and mutable [4](#0-3) .
- Attribution of the withdrawal is computed purely from whichever transaction is observed on-chain spending the withdrawal UTXO: `get_payout_txs_for_withdrawal_utxos` just joins on `bitcoin_syncer_spent_utxos` by outpoint [5](#0-4) , and `update_finalized_payouts` parses the first OP_RETURN output of *that* mined transaction to populate `payout_payer_operator_xonly_pk`, defaulting to `None` if the OP_RETURN is missing/unparsable [6](#0-5) .
- This attribution is later trusted blindly: `Verifier::is_kickoff_malicious` treats a `None` operator as "assume malicious" [7](#0-6) , and `Operator::send_asserts` requires `payout_op_xonly_pk == kickoff_data.operator_xonly_pk` to proceed with building the assertion chain feeding `create_latest_blockhash_timeout_txhandler` [8](#0-7) .

Since the withdrawer is the one who signs input 0/output 0 in the first place (it's their own `SinglePlusAnyoneCanPay` signature given to the operator off-chain), they can independently assemble a second, fully valid transaction reusing that same signed input/output, add their own fee-paying inputs, and attach any OP_RETURN they like (or none, or garbage) — then race it into a block ahead of (or instead of) the operator's actual funding transaction.

This breaks the exact invariant the question describes and matches a listed Critical impact category ("an operator reimbursed for a payout it never funded", or "an honest operator permanently unable to be reimbursed"/"collateral burned" when the OP_RETURN is destroyed). None of the listed guards (`is_deposit_valid`, `is_profitable`, `SECP.verify_schnorr`, `is_kickoff_malicious`, SPV/lc-proof verification) check that the OP_RETURN-named operator is the one who actually supplied the extra funding inputs — they all trust the OP_RETURN of whichever transaction happens to be mined.

### Title
Payout attribution trusts an unauthenticated, mutable OP_RETURN instead of the actual funder — `create_latest_blockhash_timeout_txhandler` / assertion flow credits payouts by a forgeable field - (File: `core/src/builder/transaction/operator_assert.rs`, `core/src/verifier.rs`, `core/src/operator.rs`)

### Summary
Withdrawal payout attribution (`withdrawals.payout_payer_operator_xonly_pk`) is derived solely by parsing the OP_RETURN of whichever transaction is observed spending the withdrawal UTXO on-chain, per `update_finalized_payouts`. Because the withdrawer's own `SinglePlusAnyoneCanPay` signature only commits to input 0/output 0, the withdrawer — an unprivileged party who already possesses that signature — can construct and race in a competing, self-funded transaction that reuses the same signed input/output but carries an arbitrary or unparsable OP_RETURN, permanently corrupting the operator-attribution database record that feeds `is_kickoff_malicious`, `send_asserts`, and ultimately the BitVM assertion/`LatestBlockhashTimeout` flow.

### Finding Description
Equality claimed: `withdrawals.payout_payer_operator_xonly_pk (idx=i)` == the xonly-pk of the party whose Bitcoin inputs actually funded `withdrawals` output 0 for withdrawal `i`.

Path:
1. User calls `withdraw` on Citrea, then submits `WithdrawParams` with a `SinglePlusAnyoneCanPay` signature over input 0/output 0 to an operator via gRPC `withdraw` [9](#0-8) .
2. `create_payout_txhandler` builds a payout tx with input 0 (user's dust/identifier UTXO, key-spend), output 0 (user payout), output 1 (anchor), output 2 (OP_RETURN naming the fronting operator) [1](#0-0) . Only input 0 and output 0 are covered by the user's signature due to `SinglePlusAnyoneCanPay` semantics [4](#0-3) .
3. Because the attacker (withdrawer) already possesses this signature, they can build a second, independently-funded transaction spending the same input 0, with the identical output 0, but with a different/garbage/missing OP_RETURN, and get it mined instead of/ahead of the operator's actual funding transaction (mempool/RBF race, or simply broadcasting first).
4. `update_finalized_payouts` looks up whichever tx the chain shows spending the withdrawal UTXO, via `get_payout_txs_for_withdrawal_utxos` (`bitcoin_syncer_spent_utxos` join, no txid pinning) [5](#0-4) , and blindly parses that tx's OP_RETURN to set `payout_payer_operator_xonly_pk`, defaulting to `None` on failure [6](#0-5) .
5. `is_kickoff_malicious` treats `None` as "assume malicious" [7](#0-6) , and `send_asserts` requires an exact match between DB attribution and the kicking-off operator before letting the honest funder proceed to build the assert/`LatestBlockhashTimeout` chain [8](#0-7) .

No component re-derives or checks that the additional funding inputs of the mined payout transaction actually belong to the OP_RETURN-named operator; attribution is taken purely from an unsigned field of an unauthenticated transaction.

### Impact Explanation
- If the attacker sets the OP_RETURN to name an arbitrary (possibly colluding) operator xonly-pk who never funded the payout, that operator can later kick off and pass `is_kickoff_malicious`/`send_asserts` checks, fraudulently claiming reimbursement for a withdrawal it never funded — Critical ("an operator reimbursed for a payout it never funded").
- If the attacker destroys/omits the OP_RETURN, the honest operator who actually funded (but whose broadcast tx got replaced) is permanently marked malicious by `is_kickoff_malicious`, blocking reimbursement and leading to collateral burn — Critical ("an honest operator permanently unable to be reimbursed" / "collateral burned").
- Repeatable for every withdrawal/operator pair; cost to attacker is only mining fees plus their own payout amount (which pays themselves, since output 0 goes to the withdrawer's own script pubkey).

### Likelihood Explanation
The attacker needs no privileged role — only the ability to sign a withdrawal (already required to call `withdraw`) and broadcast a competing Bitcoin transaction with sufficient fee to win the block-inclusion race against the operator's funding transaction. This requires no key compromise, no majority hashrate, and no protocol collusion — the withdrawer, who already legitimately holds their own `SinglePlusAnyoneCanPay` signature, has 100% of what's needed. Feasibility depends only on standard mempool/fee dynamics.

### Recommendation
Do not derive payout attribution from an unauthenticated OP_RETURN of an arbitrary transaction spending the withdrawal UTXO. Instead, require the operator to commit to (and later prove) the exact full payout txid (including its funding inputs) at withdrawal-request time, or bind the OP_RETURN/output-0 pairing under a signature that also covers the operator's funding inputs (e.g., using `SIGHASH_ALL` inputs from the operator co-signed with the aggregator, or a covenant tying operator identity cryptographically to the specific broadcast transaction) so that a third party cannot rewrite attribution while preserving a valid witness for input 0.

### Proof of Concept
```
cargo test -p core --test operator_assert_op_return_malleability -- --nocases
```
Plan:
1. Set up regtest e2e harness (existing `deposit_and_withdraw_e2e` helpers).
2. Register a deposit/withdrawal; obtain the user's `SinglePlusAnyoneCanPay` signature over input 0/output 0 as in `sign_withdrawal_output`.
3. Have "Operator A" begin funding/broadcasting the legitimate payout tx (real inputs, OP_RETURN = A's xonly-pk).
4. Before confirmation, construct a second transaction reusing input 0's signature/output 0, funded by the attacker's own wallet, with OP_RETURN = missing/garbage bytes (or Operator B's xonly-pk), with a higher fee rate, and broadcast/mine it first.
5. Assert `verifier_db.get_payout_info_from_move_txid` returns `operator_xonly_pk == None` (or `== B`) instead of `== A`.
6. Assert `Verifier::is_kickoff_malicious` for Operator A's subsequent legitimate kickoff returns `true` (falsely flags honest funder as malicious), demonstrating the broken binding: credited party != actual funder.

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

**File:** core/src/operator.rs (L560-637)
```rust
    pub async fn withdraw(
        &self,
        withdrawal_index: u32,
        in_signature: taproot::Signature,
        in_outpoint: OutPoint,
        out_script_pubkey: ScriptBuf,
        out_amount: Amount,
    ) -> Result<Transaction, BridgeError> {
        tracing::info!(
            "Withdrawing with index: {}, in_signature: {:?}, in_outpoint: {:?}, out_script_pubkey: {}, out_amount: {}",
            withdrawal_index,
            in_signature,
            in_outpoint,
            out_script_pubkey,
            out_amount
        );

        // Prepare input and output of the payout transaction.
        let input_prevout = self.rpc.get_txout_from_outpoint(&in_outpoint).await?;
        let input_utxo = UTXO {
            outpoint: in_outpoint,
            txout: input_prevout,
        };
        let output_txout = TxOut {
            value: out_amount,
            script_pubkey: out_script_pubkey,
        };

        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }

        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L1275-1296)
```rust
        let (payout_op_xonly_pk_opt, payout_block_hash, payout_txid, deposit_idx) = self
            .db
            .get_payout_info_from_move_txid(Some(&mut dbtx), move_txid)
            .await
            .wrap_err("Failed to get payout info from db during sending asserts.")?
            .ok_or_eyre(format!(
                "Payout info not found in db while sending asserts for move txid: {move_txid}"
            ))?;

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

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };
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

**File:** core/src/verifier.rs (L1875-1890)
```rust
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

**File:** core/src/verifier.rs (L2311-2328)
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
```
