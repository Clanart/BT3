### Title
Payout OP_RETURN operator attribution is unauthenticated, letting any funder mislabel who fronted a withdrawal - (File: `core/src/verifier.rs`)

### Summary
`Verifier::update_finalized_payouts` trusts the `operator_xonly_pk` parsed from the mined payout transaction's OP_RETURN as ground truth for "who fronted this withdrawal," but nothing on-chain binds that OP_RETURN content to the party who actually supplied the payout funds. Because the withdrawal UTXO is spent with a user-provided `SIGHASH_SINGLE|ANYONECANPAY` signature (`core/src/operator.rs:630-637`, `core/src/builder/transaction/operator_reimburse.rs:407-436`), any party that can fund the output amount can complete and broadcast a valid alternative payout transaction that keeps the committed input/output pair but writes an arbitrary xonly_pk (belonging to an operator who never touched the transaction) into the OP_RETURN, and get it mined instead of/before the real operator's version.

### Finding Description
The invariant claimed is: `operator_credited_for_withdrawal_i == party_whose_funds_paid_that_payout`.

Trace:
1. `Operator::withdraw` (`core/src/operator.rs:560-626`) builds the payout tx via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`), which sets input 0 = `withdrawal_utxo` (KeySpend with the *user's* signature) and adds an OP_RETURN with `operator_xonly_pk`. The user's signature only covers input 0 / output 0 under `SinglePlusAnyoneCanPay` sighash (`core/src/operator.rs:630-637`), so it does **not** commit to the OP_RETURN output or to any additional funding inputs, per standard `SIGHASH_SINGLE|ANYONECANPAY` semantics.
2. Whoever adds the additional funding inputs (via `fund_raw_transaction`/`sign_raw_transaction_with_wallet`, `core/src/operator.rs:652-681`) signs those inputs with default `SIGHASH_ALL`, which commits to whatever OP_RETURN *they* choose to place — there is no check anywhere that this OP_RETURN key equals the signer of those funding inputs.
3. On the verifier side, `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) simply parses whichever payout tx actually got mined for the recorded `withdrawal_utxo` (`core/src/database/verifier.rs:108-135`) and extracts `operator_xonly_pk` from its OP_RETURN with no signature check tying it to the funder (`core/src/verifier.rs:2319-2321`), then calls `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`) to persist that pubkey as the payer of record.
4. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) queries `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` and, if the DB (falsely) attributes an unhandled payout to that operator's key, automatically calls `Operator::handle_finalized_payout` (`core/src/operator.rs:839-916`), which unconditionally builds and signs a kickoff for **that operator's own presigned connector** — with no verification that this operator's wallet actually broadcast/funded the mined payout tx.
5. Separately, `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) compares `kickoff_data.operator_xonly_pk` (whichever operator actually sends a kickoff) against the DB's `operator_xonly_pk_opt` for that deposit (`core/src/verifier.rs:1882-1890`); if a third party's manipulated OP_RETURN gets mined instead of the legitimate fronting operator's tx, the legitimate operator's later kickoff is marked malicious.

Because completing/funding the payout tx does not require holding an operator identity or collateral — only enough BTC to cover the output — an unprivileged party can reuse the reusable `ANYONECANPAY` witness on `withdrawal_utxo`, add their own funding inputs, write an arbitrary existing operator's xonly_pk into the OP_RETURN, and win the race to get it mined ahead of the genuine fronting operator's transaction (e.g., via a higher fee/RBF). No existing guard (`is_deposit_valid`, `SECP.verify_schnorr` on the user's payout signature, `is_kickoff_malicious`, or any DB uniqueness constraint) validates that the OP_RETURN pubkey corresponds to whoever actually funded the transaction.

### Impact Explanation
Two concrete Critical outcomes follow directly from the database's unauthenticated attribution:
- **An operator reimbursed for a payout it never funded**: `PayoutCheckerTask` for the framed operator automatically issues a real kickoff/reimbursement claim against the deposit's escrowed BTC based solely on the forged OP_RETURN, without that operator ever having spent a satoshi.
- **An honest operator's collateral burned**: if the legitimate fronting operator's own payout tx is pre-empted (double-spend of the shared `withdrawal_utxo`) by the attacker's mislabeled version, the legitimate operator's later kickoff is flagged malicious by `is_kickoff_malicious` because the DB's recorded payer no longer matches `kickoff_data.operator_xonly_pk`.

This is repeatable for every withdrawal and against any registered operator, since the root cause (OP_RETURN content not bound to the funder's identity by any signature check in `update_finalized_payouts`) is systemic, not deposit- or operator-specific.

### Likelihood Explanation
The attacker needs no privileged role, key share, or TLS certificate — only the ability to observe/replay a withdrawal's `(in_signature, in_outpoint, out_script_pubkey, out_amount)` (obtainable via the public, unauthenticated aggregator `Withdraw` RPC path, or once any competing payout tx appears in the mempool) and enough BTC to fully fund the output amount plus fees so the alternative transaction is valid and can win the fee race. This is a real capital cost (~bridge amount) but not a "theft" — it is griefing/sabotage capital, well within reach of a well-funded adversary (e.g., a rival operator or a party wanting to burn a specific operator's collateral). No majority hashrate, verifier compromise, or protocol-level exploit is required — only standard Bitcoin transaction/fee-market mechanics exploiting the unauthenticated OP_RETURN.

### Recommendation
Do not trust the OP_RETURN pubkey as sole proof of who funded a payout. Instead, require the operator claiming reimbursement to additionally sign (or otherwise cryptographically commit to) the exact payout transaction using a key/mechanism verifiable against the operator's known presigned kickoff/collateral — e.g., have the operator's own DepositSign/kickoff transaction embed a signature over the payout txid using their operator key, and have `update_finalized_payouts`/`handle_finalized_payout` verify that signature rather than trusting free-form OP_RETURN bytes written by whoever completed the tx. Alternatively, require the additional funding inputs of the payout transaction to be spendable only by outputs traceable to a specific operator's registered wallet key, and validate that linkage before crediting.

### Proof of Concept
`cargo test` plan (regtest, `MockCitreaClient`, no mainnet/live Citrea):
1. Run a normal deposit + `insert_withdrawal_utxo`/`update_withdrawal_utxo_from_citrea_withdrawal` flow to establish `withdrawal_utxo` for index `idx` (as in `core/src/test/manual_reimbursement.rs` / `core/src/test/deposit_and_withdraw_e2e.rs`).
2. Generate the user's `SinglePlusAnyoneCanPay` withdrawal signature/output via `generate_withdrawal_transaction_and_signature`.
3. Construct transaction A: operator0's legitimate payout (via `create_payout_txhandler` with `operator0.signer.xonly_public_key`), fund with operator0's wallet, but do **not** broadcast yet.
4. Construct transaction B: reuse the same input 0 witness/signature over the same output 0, but fund it with a third party's own UTXOs and set the OP_RETURN to `operator1.signer.xonly_public_key` (an operator who never participated). Broadcast B first and mine it.
5. Run block sync so `update_finalized_payouts` processes the block containing B.
6. Assert: `db.get_payout_info_from_move_txid(move_txid)` returns `Some(operator1_xonly_pk, ...)` — i.e., `operator_xonly_pk_opt == operator1.signer.xonly_public_key` even though operator1 funded nothing — violating `operator_credited_for_withdrawal_i == party_whose_funds_paid_that_payout`.
7. Optionally continue: run `PayoutCheckerTask` for operator1 and assert it calls `handle_finalized_payout`, producing a real kickoff txid for operator1 despite operator1 never broadcasting a payout tx, demonstrating "operator reimbursed for a payout it never funded." [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/verifier.rs (L1857-1915)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

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

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
    }
```

**File:** core/src/verifier.rs (L2312-2343)
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

**File:** core/src/operator.rs (L620-637)
```rust
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

**File:** core/src/operator.rs (L839-916)
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

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

        // get signed txs,
        let kickoff_data = KickoffData {
            operator_xonly_pk: self.signer.xonly_public_key,
            round_idx,
            kickoff_idx,
        };

        let payout_tx_blockhash = payout_tx_blockhash.as_byte_array().last_20_bytes();

        #[cfg(test)]
        let payout_tx_blockhash = self
            .config
            .test_params
            .maybe_disrupt_payout_tx_block_hash_commit(payout_tx_blockhash);

        let context = ContractContext::new_context_for_kickoff(
            kickoff_data,
            deposit_data,
            self.config.protocol_paramset(),
        );

        let signed_txs = create_and_sign_txs(
            self.db.clone(),
            &self.signer,
            self.config.clone(),
            context,
            Some(payout_tx_blockhash),
            Some(dbtx),
        )
        .await?;

```

**File:** core/src/database/verifier.rs (L253-280)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
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
