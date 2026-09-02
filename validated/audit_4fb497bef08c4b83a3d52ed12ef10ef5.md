### Title
Payout transaction's OP_RETURN operator-attribution output is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing anyone to front-run and reassign fronting credit for a withdrawal - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx` created for a Citrea withdrawal has three outputs: the user's payout, an anchor, and an `OP_RETURN` output that records which operator's x-only pubkey fronted the withdrawal. The user's signature is enforced to use `TapSighashType::SinglePlusAnyoneCanPay`, which under BIP341 only commits to the single input and the single output at index 0 (the user payout). The `OP_RETURN` attribution output (and the anchor) are **not covered** by this signature, and `ANYONECANPAY` additionally allows arbitrary extra inputs to be added. This means any unprivileged party who observes the broadcast (or otherwise obtains) the signed input can construct a competing, higher-fee transaction that pays the identical user output but substitutes an arbitrary x-only pubkey in the `OP_RETURN` output, then win the mempool race (RBF) against the honest operator's broadcast. This directly breaks the binding "operator credited for fronting the payout == the party that actually funded/broadcast it," analogous to the Augur report's fingerprint front-running that decouples fee attribution from the real paying party.

### Finding Description
- `create_payout_txhandler` builds the payout transaction with output 0 = user payout, output 1 = anchor, output 2 = `OP_RETURN` pushing `operator_xonly_pk.serialize()`, and signs only the single key-spend input with the user's supplied signature: [1](#0-0) 
- The sighash type is strictly enforced to be `SinglePlusAnyoneCanPay` (a `SIGHASH_SINGLE|ANYONECANPAY`-style commitment that binds only input 0 and output 0, and permits other inputs/outputs to be added or changed): [2](#0-1) 
- The operator's `withdraw` flow re-derives the sighash purely from `in_signature.sighash_type` and only verifies the schnorr signature against that sighash - it never re-checks that the `OP_RETURN` output actually matches its own `self.signer.xonly_public_key` inside a signed/committed context, then funds and broadcasts the tx via RBF: [3](#0-2) [4](#0-3) 
- Attribution of "who fronted the payout" is derived later purely by parsing the `OP_RETURN` of whichever payout transaction actually confirms on-chain, with no cross-check against who paid the transaction fee or broadcast it: [5](#0-4) 
- This attribution is written to `withdrawals.payout_payer_operator_xonly_pk` and is the sole key used to decide which operator is allowed to proceed with kickoff/reimbursement for that withdrawal: [6](#0-5) [7](#0-6) [8](#0-7) 

Because the `OP_RETURN` value is outside the cryptographic commitment of the user's signature, and `ANYONECANPAY` permits adding attacker-funded inputs/outputs freely, any party (not just an operator) who sees the honest operator's broadcast payout transaction in the mempool can clone it, keep the committed input+output0 witness data intact, replace the `OP_RETURN` pubkey with an arbitrary value, add their own funding input for a higher fee, and RBF-replace the honest operator's transaction before it confirms.

### Impact Explanation
This breaks the custody-attribution binding "operator credited for the payout == the party that funded/broadcast it." Concretely:
- An attacker can attribute the fronted-withdrawal credit to any chosen operator xonly pubkey, including an operator that never funded/broadcast anything, causing that operator to be later reimbursed via kickoff/round machinery for a payout it did not front (Critical: "an operator reimbursed for a payout it never funded").
- Conversely, the honest operator who actually fronted the funds (and paid the transaction fee to get their own version confirmed) can have their transaction replaced/out-raced, so their `OP_RETURN` credit never lands on-chain, permanently denying them the ability to be reimbursed for a withdrawal they genuinely funded (Critical: "an honest operator permanently unable to be reimbursed").

### Likelihood Explanation
The attack requires only observing a broadcast, unconfirmed transaction in the Bitcoin mempool (or any relay) and constructing/broadcasting a competing transaction with a higher fee - no verifier, operator, watchtower, or privileged role is required, satisfying the "unprivileged attacker" constraint. The `SinglePlusAnyoneCanPay` sighash type is intentionally used (and enforced) here specifically to allow fee-bumping/funding flexibility, but this same design choice is what leaves the `OP_RETURN` attribution output unauthenticated. Any general mempool-observing actor with an unconfirmed-transaction replacement capability (standard RBF) can attempt this at low cost relative to the withdrawal amount at stake.

### Recommendation
Bind the operator-attribution `OP_RETURN` output to the same signature that authorizes the payout, or otherwise make attribution independent of transaction-malleable output data:
- Use a sighash type that also commits to the `OP_RETURN` output (e.g., have the user's signature cover all outputs, or have a second, operator-signed commitment over the specific `OP_RETURN` content that is itself checked before crediting `payout_payer_operator_xonly_pk`).
- Alternatively, attribute the fronting credit based on which operator's key actually authorized/funded the additional inputs (verifiable on-chain), rather than trusting an unauthenticated `OP_RETURN` push that any party can rewrite in a replacement transaction.
- At minimum, require the operator's own kickoff/reimburse flow to prove it funded the specific confirmed payout txid (e.g., by matching fee-payer inputs it controls), rather than trusting `OP_RETURN` content alone as parsed in `update_finalized_payouts`.

### Proof of Concept
1. Operator A observes withdrawal `id=N` and constructs `payout_tx_A`: input = user's UTXO (spent via the user's `SinglePlusAnyoneCanPay` signature), output0 = user's payout, output1 = anchor, output2 = `OP_RETURN(A_xonly_pk)`; broadcasts via RBF-fundable flow (`core/src/operator.rs:620-691`).
2. Attacker M, monitoring the mempool, extracts the signed input (witness data is valid for any transaction that preserves input0's prevout and output0 exactly, per the `SinglePlusAnyoneCanPay` commitment enforced at `core/src/rpc/parser/operator.rs:161-203`).
3. M builds `payout_tx_M`: same input (same witness), same output0 (user payout, byte-for-byte identical script_pubkey/amount as required by the signature), but a modified output2 `OP_RETURN(B_xonly_pk)` (B = any operator M wants credited, possibly A's competitor or a colluding operator), funded with M's own extra input to pay a higher fee, and signals RBF.
4. M broadcasts `payout_tx_M`, which replaces `payout_tx_A` in miners' mempools due to higher fee, and gets confirmed instead.
5. `update_finalized_payouts` parses the confirmed transaction's `OP_RETURN` and records `payout_payer_operator_xonly_pk = B_xonly_pk` (`core/src/verifier.rs:2296-2350`), so operator B's kickoff/reimburse path is later unlocked (`core/src/task/payout_checker.rs:39-79`) even though B never funded or broadcast anything, while operator A - who actually paid the fee and fronted the payout first - is denied attribution and reimbursement.

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

**File:** core/src/rpc/parser/operator.rs (L161-203)
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

    let input_outpoint: OutPoint = params
        .input_outpoint
        .ok_or_else(error::input_ended_prematurely)?
        .try_into()?;

    let users_intent_script_pubkey = ScriptBuf::from_bytes(params.output_script_pubkey);

    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
}
```

**File:** core/src/operator.rs (L614-637)
```rust
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

**File:** core/src/operator.rs (L639-691)
```rust
        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
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

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;

        Ok(signed_tx)
```

**File:** core/src/verifier.rs (L2296-2343)
```rust
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
