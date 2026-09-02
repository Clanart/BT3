This is a genuine analog. The withdrawal signature scheme uses `SIGHASH_SINGLE | ANYONECANPAY`, which leaves the payout transaction's `OP_RETURN` output (carrying the fronting operator's identity) and any additional funding inputs completely unauthenticated by the user. That gap lets the "operator credited" for reimbursement diverge from "the party that actually paid," mirroring the reported class of bug (a refund/credit binding that isn't actually enforced on-chain).

### Title
Unauthenticated payout OP_RETURN operator-identity field lets a third party misattribute reimbursement credit to an uninvolved operator - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` embeds the fronting operator's x-only pubkey in an `OP_RETURN` output (output index 2) that is never covered by the user's withdrawal signature, because the withdrawal signature is required to use `TapSighashType::SinglePlusAnyoneCanPay`. [1](#0-0) [2](#0-1)  `SinglePlusAnyoneCanPay` only commits to input 0 and the output at the same index (output 0, the user's payout), leaving the anchor output and the `OP_RETURN` operator-identity output, as well as any additional funding inputs added later, completely unsigned. [3](#0-2)  Because verifiers determine "who fronted the withdrawal" purely by parsing whatever `OP_RETURN` value ends up in the confirmed payout tx, an unprivileged party can reuse the user's public signature/output-0 pair, substitute an arbitrary (or another legitimate operator's) x-only pubkey in the `OP_RETURN`, fund the transaction themselves, and get it confirmed — causing an operator who never funded the withdrawal to be credited and subsequently reimbursed.

### Finding Description
The payout flow works as follows:
1. The user pre-signs only the withdrawal input with `SinglePlusAnyoneCanPay`, which is explicitly enforced server-side. [4](#0-3) 
2. `create_payout_txhandler` builds the transaction with three outputs — user payout (index 0), anchor (index 1), and `OP_RETURN` containing `operator_xonly_pk` (index 2) — then attaches the user's key-spend witness for input 0 only. [5](#0-4) 
3. Per BIP-341 `SIGHASH_SINGLE|ANYONECANPAY` semantics (implemented via `Prevouts::One` in `calculate_pubkey_spend_sighash`), the signature commits only to output index 0 and the single spent input; it says nothing about outputs 1/2 or about whatever additional funding inputs get appended later via `fund_raw_transaction`. [6](#0-5) [7](#0-6) 
4. When a block confirms, the verifier's block-sync logic determines the "payer" purely by decoding the `OP_RETURN` bytes of whichever payout tx actually confirmed, with no check that the named operator's funds (or signature) were involved in constructing/funding the transaction: [8](#0-7) 
5. That unauthenticated value is persisted as `payout_payer_operator_xonly_pk` and later used verbatim to select which operator's reimbursement flow to trigger. [9](#0-8) [10](#0-9) 
6. `PayoutCheckerTask`/`handle_finalized_payout` then advances that operator's own round/kickoff/reimburse-tx chain to actually reimburse them from their collateral, with no cross-check that this operator supplied the peg-out funds. [11](#0-10) [12](#0-11) 

Consequently, the equality the protocol is supposed to guarantee — "the operator credited for reimbursement == the party that actually fronted the withdrawal" — is not enforced anywhere on-chain or in verifier logic; it is derived from unsigned transaction bytes that any observer can rewrite before confirmation.

### Impact Explanation
This breaks the custody binding directly listed as a critical impact: an operator can end up "reimbursed for a payout it never funded." If a third party (or another operator) intercepts the broadcast/mempool-visible payout transaction, keeps input 0 and output 0 exactly as signed, but swaps in a different `OP_RETURN` operator pubkey and funds the rest of the transaction themselves, whichever operator's key ends up in the confirmed `OP_RETURN` will have its own pre-established round/kickoff/reimburse chain triggered and receive BTC back from its collateral for a withdrawal it never funded. This is a bridge-custody value-attribution break, not merely a griefing/DoS issue.

### Likelihood Explanation
Exploitation requires only mempool visibility of an unconfirmed payout transaction (or any other way of observing the user's `SinglePlusAnyoneCanPay` signature and output-0 script) plus the ability to fund and broadcast a competing/replacing transaction with higher fee — capabilities available to any unprivileged network participant, not any protocol role (verifier/operator/aggregator/watchtower). No key compromise or majority hashrate is needed.

### Recommendation
Require the withdrawal/payout-related signature (or a companion signature) to cover the `OP_RETURN` operator-identity output and, ideally, all inputs of the payout transaction (e.g. use `SIGHASH_ALL` or otherwise cryptographically bind the operator identity to the same commitment the user signs, or require the operator itself to co-sign a commitment that binds its own pubkey to that specific payout transaction before verifiers accept it as attribution evidence).

### Proof of Concept
1. Operator A (or an unrelated actor with a funded wallet) observes the mempool for the payout transaction constructed by `create_payout_txhandler`/`operator.rs::withdraw` — it contains only a valid witness for input 0 (`SinglePlusAnyoneCanPay`) and a fixed output 0 (the user's payout script/amount).
2. The actor rebuilds a transaction with the identical input 0 (same witness, since it isn't invalidated by ANYONECANPAY) and output 0, but replaces output 2 (`OP_RETURN`) with a different registered operator B's `xonly_pk`, and funds the remaining value/fees from their own coins (mirroring `fund_raw_transaction`'s `add_inputs: true` behavior used by legitimate operators).
3. This transaction, once broadcast with a competitive fee, is equally valid (the original user signature still verifies) and can be confirmed instead of/before the legitimate one.
4. `update_finalized_payouts` parses the confirmed tx's `OP_RETURN`, records `payout_payer_operator_xonly_pk = B`. [13](#0-12) 
5. Operator B's `PayoutCheckerTask` picks this up via `get_first_unhandled_payout_by_operator_xonly_pk` and proceeds through `handle_finalized_payout` → kickoff → reimburse_tx, reimbursing B from its own collateral for a withdrawal it never funded. [14](#0-13)

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-435)
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

**File:** core/src/builder/transaction/txhandler.rs (L210-233)
```rust
    pub fn calculate_pubkey_spend_sighash(
        &self,
        txin_index: usize,
        sighash_type: TapSighashType,
    ) -> Result<TapSighash, BridgeError> {
        let prevouts_vec: Vec<&TxOut> = self
            .txins
            .iter()
            .map(|s| s.get_spendable().get_prevout())
            .collect();
        let mut sighash_cache: SighashCache<&bitcoin::Transaction> =
            SighashCache::new(&self.cached_tx);
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

**File:** core/src/operator.rs (L652-674)
```rust
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
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

**File:** core/src/verifier.rs (L2312-2342)
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
