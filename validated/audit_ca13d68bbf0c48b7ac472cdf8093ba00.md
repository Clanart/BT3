DEFAULT_SEQUENCE is `Sequence::ENABLE_RBF_NO_LOCKTIME`, confirming the payout transaction's withdrawal input signals opt-in Replace-By-Fee (BIP125), so a conflicting transaction spending the same input can validly replace the honest operator's broadcast in the mempool given a sufficient fee bump — no majority hashrate or non-standard mining behavior is required, consistent with the question's "mine ahead of the honest tx" framing.

### Title
Honest operator's Reimburse path can be permanently destroyed via SIGHASH_SINGLE|ANYONECANPAY payout-tx malleation - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` signs input 0 with `SpendPath::KeySpend` under a user-supplied `taproot::Signature` whose sighash flag (verified in `Operator::withdraw` via `SECP.verify_schnorr`) can be `SinglePlusAnyoneCanPay`, which per BIP341 only commits to input 0 and output 0. Any unprivileged observer of the mempool can copy input 0's witness and output 0, attach their own fee-paying input(s), replace the OP_RETURN (output 2) with garbage, and get this transaction mined instead (using RBF, since `DEFAULT_SEQUENCE = Sequence::ENABLE_RBF_NO_LOCKTIME`), causing `Verifier::update_finalized_payouts` to record `payout_payer_operator_xonly_pk = NULL` for that withdrawal and permanently blocking the honest operator's reimbursement lookup.

### Finding Description
The binding claimed correct is: `operator_xonly_pk` stored in `withdrawals.payout_payer_operator_xonly_pk` for a given withdrawal idx == the xonly pk of the operator who actually funded output 0 of the transaction that spends that withdrawal's committed UTXO on-chain.

`get_payout_txs_for_withdrawal_utxos` [1](#0-0)  determines "the payout tx" for a withdrawal purely by which transaction spends the pre-committed `withdrawal_utxo_txid`/`vout` in a given block — it does not pin a specific pre-known payout txid. `Verifier::update_finalized_payouts` then extracts the operator xonly pk solely from that mined transaction's first OP_RETURN output via `get_first_op_return_output`/`parse_op_return_data` [2](#0-1) , defaulting to `None`/NULL if missing or malformed, and writes it unconditionally via `update_payout_txs_and_payer_operator_xonly_pk` [3](#0-2) .

`create_payout_txhandler` builds input 0 as a key-spend of the user's withdrawal UTXO and signs it with the caller-supplied signature/sighash type [4](#0-3) . `Operator::withdraw` verifies this signature against the sighash computed for whatever `in_signature.sighash_type` the caller (the withdrawing user via gRPC, ultimately attacker-controlled data since they choose the Citrea withdrawal UTXO and signature) provides [5](#0-4) ; `calculate_pubkey_spend_sighash` explicitly supports `SinglePlusAnyoneCanPay` and restricts the committed prevout set to just input 0 for that flag [6](#0-5) . Per BIP341, `SIGHASH_SINGLE|ANYONECANPAY` commits only to input 0's outpoint/prevout and to output 0 — nothing else, including additional inputs, the anchor output, or the OP_RETURN output, is covered.

Because the withdrawal input uses `DEFAULT_SEQUENCE = Sequence::ENABLE_RBF_NO_LOCKTIME` [7](#0-6) , the honest operator's broadcast tx is RBF-opt-in, so a conflicting transaction spending the same withdrawal outpoint is a valid BIP125 replacement candidate given enough fee, requiring no privileged mining power — an unprivileged attacker only needs to observe the honest tx in the mempool and rebroadcast a higher-fee conflicting transaction (or, as the question specifies, simply have it mined in a regtest test setup).

Exploit flow: attacker observes the honest operator's broadcast payout tx in mempool, extracts input 0's outpoint+witness (still valid because the signature doesn't cover the rest of the tx) and output 0 (kept byte-identical, since SIGHASH_SINGLE pins it), adds their own fee input, and swaps output 2 (OP_RETURN, normally `op_return_txout(operator_xonly_pk)`) for an empty/garbage OP_RETURN or removes it. This new tx is signature-valid and gets confirmed (via RBF or by simply being mined first). `update_finalized_payouts` then finds no valid xonly pk in the OP_RETURN and writes `payout_payer_operator_xonly_pk = NULL` for that withdrawal index, even though the operator actually fronted the withdrawal's output 0 funds via a different, no-longer-confirmed transaction.

No existing guard prevents this: `is_kickoff_malicious` only checks operator/kickoff-time payout info already in the DB against the kickoff's claimed operator (post-hoc, and would correctly flag the operator as unable to prove payout, not detect or reverse the attack) [8](#0-7) ; `SECP.verify_schnorr` in `Operator::withdraw` only checks the signature is valid for the sighash the operator itself computed at broadcast time — it cannot know in advance that a different mined tx (with the same input 0/output 0) will confirm instead [9](#0-8) . `Operator::is_profitable` (fee/amount check) and `verify_storage_proofs`/`SPV::verify` in the circuits validate the withdrawal's Citrea-side amounts and its inclusion proof, but do not constrain which transaction (of several signature-valid candidates spending the same UTXO) actually confirms, nor do they re-validate the OP_RETURN's authenticity against the party who funded the tx.

### Impact Explanation
The honest operator who genuinely fronted the withdrawal (paid out output 0 of a transaction spending the committed withdrawal UTXO) becomes permanently unable to be credited: `payout_payer_operator_xonly_pk` is set to NULL for that withdrawal idx, so `get_first_unhandled_payout_by_operator_xonly_pk` [10](#0-9)  — which filters `WHERE ... payout_payer_operator_xonly_pk = $1` — will never match that operator's xonly pk for this idx, so `PayoutCheckerTask::run_once` [11](#0-10)  never processes it and the operator's Reimburse path for this withdrawal is permanently lost. This is repeatable per withdrawal (any operator, any deposit) whenever a payout is broadcast to mempool before confirmation, giving the attacker a griefing tool with no capital requirement beyond fees; it matches the Critical category "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Preconditions: the attacker must observe the honest operator's payout transaction in the public mempool (trivially available, all Bitcoin transactions are broadcast pre-confirmation) and race it with a higher-fee (or otherwise consensus-valid and preferentially mined) conflicting transaction spending the same withdrawal outpoint. `DEFAULT_SEQUENCE` already signals RBF eligibility, removing the need for non-standard mempool policies. Attacker cost is limited to Bitcoin transaction fees for the replacement transaction (no protocol collateral, deposit, or gRPC privilege required); the operator's own withdrawal input value and output 0 are unaffected (user still gets paid), so this is a pure griefing/DoS-of-reimbursement attack, feasible with a single Bitcoin full node and wallet, and repeatable against every operator/withdrawal that uses this signature scheme.

### Recommendation
Do not accept `SinglePlusAnyoneCanPay` (or any `ANYONECANPAY` variant) for the withdrawal-input user signature in `create_payout_txhandler`/`Operator::withdraw`; require `SIGHASH_ALL` (or otherwise a sighash flag that commits to every input and output of the payout transaction, including the operator's OP_RETURN identifying output) so no unprivileged third party can rebuild a validly-signed but differently-purposed transaction from the same signature. Additionally, `update_finalized_payouts` should not silently accept a mined transaction with a missing/garbled OP_RETURN as authoritative proof of "no operator fronted this" when a signature-compatible honest broadcast previously existed; consider binding the payout identity to information committed by the signature itself rather than to a mutable, uncommitted output.

### Proof of Concept
```
cargo test --package clementine-core payout_op_return_malleation_griefs_reimbursement -- --nocapture
```
Test plan:
1. Set up a regtest environment with one operator A and a completed deposit/withdrawal (Citrea-side `withdraw` called with a `SinglePlusAnyoneCanPay` signature, matching `generate_withdrawal_transaction_and_signature` in `core/src/test/common/setup_utils.rs`).
2. Call `Operator::withdraw` to obtain the signed `payout_txhandler` (honest tx `tx_honest`), capturing its `input[0]` witness and `output[0]`.
3. Build `tx_attacker` reusing the same `input[0]` (outpoint + witness) at index 0, an attacker-funded extra input, `output[0]` byte-identical to `tx_honest`, and `output[2]` replaced with an empty/garbage OP_RETURN.
4. Mine `tx_attacker` in a block (skip `tx_honest`).
5. Run bitcoin sync / `Verifier::update_finalized_payouts` and assert via `Database::get_payout_info_from_move_txid` that `payout_payer_operator_xonly_pk` is `None` for that withdrawal idx (equality check: expected operator A's xonly pk vs. actual `None` — binding broken).
6. Run `PayoutCheckerTask::run_once` for operator A and assert it returns `Ok(false)` (never finds the unhandled payout), confirming operator A can never reach `get_first_unhandled_payout_by_operator_xonly_pk` for this withdrawal.

### Citations

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

**File:** core/src/verifier.rs (L2312-2328)
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

**File:** core/src/builder/transaction/txhandler.rs (L27-27)
```rust
pub const DEFAULT_SEQUENCE: Sequence = Sequence::ENABLE_RBF_NO_LOCKTIME;
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

**File:** core/src/task/payout_checker.rs (L39-111)
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;

        Ok(true)
    }
```
