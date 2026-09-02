## No vulnerability found for this question.

The binding claimed to be broken is: *"the number of vault UTXOs spent for withdrawal index i == 1"* must always hold, and the report claims two back-to-back `optimistic_payout` calls can both be fully signed and later both broadcast/rebroadcast, producing two "fully-signed spends" of the same withdrawal.

Tracing the code confirms both requests can indeed be fully signed concurrently: `is_utxo_spent(&input_outpoint)` only checks the *withdrawal UTXO* (input 0), not the *deposit-in-move UTXO* (input 1, the actual N-of-N vault funds), and there is no DB row, lock, or uniqueness constraint that marks a `deposit_id`/`move_txid` as "already fully signed" before broadcast [1](#0-0) [2](#0-1) . `payout_txid`/`payout_tx_blockhash` are only written after on-chain confirmation is observed (via `update_payout_txs_and_payer_operator_xonly_pk`), not at signing time [3](#0-2) .

However, the input-1 sighash used for the N-of-N MuSig2 signature is `TapSighashType::Default` (equivalent to `SIGHASH_ALL`), which commits to **every** input and output of the transaction, including `input_outpoint` (input 0) and its owner's `output_script_pubkey`-independent key-spend witness [4](#0-3) . Because both candidate transactions spend the *identical* `input_outpoint` (input 0) as a required, fixed input (verified against the DB-recorded `withdrawal_utxo` for that `deposit_id` at [5](#0-4) ), broadcasting one on Bitcoin **necessarily** consumes both input 0 and input 1 (the vault UTXO) atomically in a single transaction. Bitcoin's UTXO consensus rules then make the second signed transaction permanently invalid the instant the first confirms, because its input 0 no longer exists — it is not merely "outcompeted," it is consensus-rejected forever (barring the confirming transaction itself being reorged out, which is a generic Bitcoin-reorg exposure identical to what exists with a *single* signed payout, not something created uniquely by double-signing).

So while the aggregator/verifiers can be induced to *produce* two distinct final N-of-N signatures for the same `deposit_id` with different `output_script_pubkey`s (a genuine gap in idempotency), the actual bridge invariant — at most one confirmed spend of the vault UTXO per withdrawal index — is enforced by Bitcoin's own double-spend rule, since both candidate payouts share the exact same `input_outpoint` as input 0 and that same outpoint can only be consumed by whichever transaction confirms first. The "second fully-signed tx the attacker can rebroadcast after a reorg" grants no capability beyond what a bare reorg of a single honestly-signed payout already exposes (the withdrawal UTXO becomes unspent again and a fresh, freely-choosable-destination request could be resigned at that point regardless of whether a backup signature was pre-computed). No BTC actually leaves the vault twice, no operator is reimbursed for an unfunded payout, and no party's collateral is burned as a direct, demonstrable consequence of this double-signing gap — the described PoC would show two valid signatures existing, but not a `cargo test` producing two *successful, non-conflicting* on-chain spends of the same vault UTXO, which is what the impact category requires.

### Citations

**File:** core/src/rpc/aggregator.rs (L1032-1042)
```rust
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self
            .rpc
            .is_utxo_spent(&input_outpoint)
            .await
            .map_to_status()?
        {
            return Err(Status::invalid_argument(format!(
                "Withdrawal utxo is already spent: {input_outpoint:?}",
            )));
        }
```

**File:** core/src/rpc/aggregator.rs (L1063-1071)
```rust
            let withdrawal_utxo = self
                .db
                .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
                .await?;
            if withdrawal_utxo != input_outpoint {
                return Err(Status::invalid_argument(format!(
                    "Withdrawal utxo is not correct: {withdrawal_utxo:?} != {input_outpoint:?}",
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L1194-1198)
```rust
            let sighash = opt_payout_txhandler.calculate_script_spend_sighash_indexed(
                1,
                0,
                bitcoin::TapSighashType::Default,
            )?;
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L459-491)
```rust
pub fn create_optimistic_payout_txhandler(
    deposit_data: &mut DepositData,
    input_utxo: UTXO,
    output_txout: TxOut,
    user_sig: taproot::Signature,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    let move_txhandler: TxHandler = create_move_to_vault_txhandler(deposit_data, paramset)?;
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::NotStored,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::non_ephemeral_anchor_output(),
        ))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    Ok(txhandler)
```

**File:** core/src/database/verifier.rs (L199-251)
```rust
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
