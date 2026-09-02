This confirms the vulnerability holds: `get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:168-196) identifies "the" payout tx purely by which transaction spends the `withdrawal_utxo` on-chain (join against `bitcoin_syncer_spent_utxos`), with no requirement that it match the txid the operator originally broadcast. Whatever transaction actually spends that specific UTXO — honest operator's or attacker's rewritten variant — is accepted as *the* payout, and its (unauthenticated) OP_RETURN determines `payout_payer_operator_xonly_pk`. [1](#0-0) 

### Title
Payout attribution forgeable via SIGHASH_SINGLE|ANYONECANPAY rewriting of unsigned OP_RETURN/funding inputs - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx`'s user signature only commits to input 0 and output 0 under the mandatory `SinglePlusAnyoneCanPay` sighash, leaving the OP_RETURN operator-attribution output, the anchor output, and all additional funding inputs completely unauthenticated. Anyone who learns the withdrawal signature/outpoint (via public mempool observation of the first broadcast, or via the aggregator's plaintext fan-out) can rebuild a variant funded from their own wallet with an arbitrary OP_RETURN operator key, and race it into a block ahead of the honest operator's original transaction.

### Finding Description
Binding claimed: `payout_payer_operator_xonly_pk` (the operator pubkey verifiers store after parsing the confirmed payout tx's OP_RETURN) == the xonly public key of the party that actually funded/broadcast the payout.

`create_payout_txhandler` builds a 3-output transaction (payout output, anchor, `op_return_txout(operator_xonly_pk)`) and signs only input 0 with the user's signature [2](#0-1)  The sighash type is force-normalized/enforced to `SinglePlusAnyoneCanPay` [3](#0-2) . `calculate_pubkey_spend_sighash`/`calculate_sighash_txin` use `Prevouts::One` for this sighash type, meaning only input 0's prevout and output 0 are committed by the signature — the anchor output, the OP_RETURN, and any number of extra inputs are unconstrained [4](#0-3) . `Operator::withdraw` funds the tx via `fund_raw_transaction`, which adds the operator's own wallet UTXOs as additional inputs before broadcasting [5](#0-4) .

Verifiers later determine "the" payout transaction for a withdrawal purely by which tx spends the specific `withdrawal_utxo` on-chain, with no txid pinning to the operator's originally-broadcast variant [1](#0-0) . `update_finalized_payouts` then parses that confirmed tx's first OP_RETURN to set `payout_payer_operator_xonly_pk` [6](#0-5) , with no cross-check against who funded the extra inputs. This value subsequently gates both `Operator::validate_payer_is_operator` (an operator can only fetch reimbursement txs if the stored payer pk equals its own) [7](#0-6)  and `Verifier::is_kickoff_malicious` (a kickoff is judged non-malicious only if the stored payer pk equals `kickoff_data.operator_xonly_pk`) [8](#0-7) .

Exploit flow: attacker observes the honest operator A's payout tx (via mempool `getrawmempool`/`sendrawtransaction` visibility, or via the aggregator's `Withdraw` gRPC fan-out at core/src/rpc/aggregator.rs:1811-1887 if unencrypted). Attacker constructs `payout_tx'` keeping input 0 and output 0 identical (satisfying the withdrawal and preserving the valid user signature), replaces operator A's funding inputs with the attacker's own funded inputs, and rewrites the OP_RETURN to name a different xonly pubkey (e.g., operator B, a rival operator, or any arbitrary 32 bytes that happens to parse as a valid xonly key). Attacker broadcasts with a higher fee/faster propagation and wins the confirmation race.

Consequence: once `payout_tx'` confirms, `payout_payer_operator_xonly_pk` is set to the attacker-chosen key, not A. Operator A's later `validate_payer_is_operator` check fails permanently ("Payer is not own operator for deposit") — A funded a real user withdrawal out of pocket and can never be reimbursed. If the named key belongs to a real operator B who never funded anything, B is misattributed as payer, and B's kickoff would (incorrectly) pass `is_kickoff_malicious`'s equality check, letting B claim reimbursement for a payout it never fronted.

### Impact Explanation
- An honest operator (A) permanently unable to be reimbursed for a withdrawal it genuinely funded — Critical impact category.
- A different operator (B) reimbursed for a payout it never funded — Critical impact category.
- Attribution corruption is deterministic and repeatable per withdrawal/deposit; any operator's payout is vulnerable the moment it hits the public mempool, regardless of aggregator TLS/`client_verification` configuration, since Bitcoin transactions are public pre-confirmation.
- Blast radius: every withdrawal processed by every operator using the standard (non-optimistic) payout path is exposed.

### Likelihood Explanation
No special deployment misconfiguration is required — the race is winnable purely via public mempool observation and a fee bump, standard Bitcoin RBF/relay behavior. The attacker's cost is funding the withdrawal output amount plus fees themselves (since output 0's value/script is fixed by the signature and must be preserved to still satisfy the Citrea withdrawal), which is a real but bounded cost proportional to the withdrawal size; it is purely a griefing cost with no reimbursement to the attacker, but it reliably destroys the target operator's ability to be reimbursed and/or frames another operator, which is a severe repeatable griefing primitive against operators' economic guarantees.

### Recommendation
Bind the OP_RETURN operator-attribution output (and ideally the funding inputs) into the signed message: either require the user's signature to use `AllPlusAnyoneCanPay`/`Default` covering all outputs (including OP_RETURN) rather than `SinglePlusAnyoneCanPay`, or have verifiers additionally require/verify that the transaction's remaining inputs are provably controlled by the operator named in the OP_RETURN (e.g., via a covenant/second signature over the OP_RETURN+funding inputs) before crediting `payout_payer_operator_xonly_pk`. At minimum, track and pin the exact txid the operator submitted via TxSender, and only trust attribution for that specific txid — treat any other transaction spending the withdrawal UTXO as a hostile substitution requiring manual reconciliation rather than automatic operator crediting.

### Proof of Concept
```
cargo test -p clementine-core --test operator_payout_attribution_forgery -- --nocapture
```
Plan:
1. Set up regtest with two bitcoind RPC connections ("honest operator" and "attacker"), and a test DB per verifier.
2. Register a withdrawal (`update_withdrawal_utxo_from_citrea_withdrawal`) and generate a `SinglePlusAnyoneCanPay` signature over `(withdrawal_utxo, payout_output)` as in `generate_withdrawal_transaction_and_signature` (core/src/test/common/setup_utils.rs:439-449).
3. Have the "honest operator" build `create_payout_txhandler(..., operator_A_xonly_pk, user_sig, ...)`, fund it via its wallet, and broadcast to the shared regtest mempool (do NOT mine yet).
4. As "attacker", fetch the mempool tx via `getrawmempool`/`getrawtransaction`, extract input 0 and output 0, and build a new unsigned tx: input 0 = original signed input, output 0 = original output unchanged, OP_RETURN = `operator_B_xonly_pk` (a different, uninvolved xonly pk), fund additional inputs from the attacker's own wallet, set a higher fee rate, and re-attach the untouched witness for input 0. Assert `SECP.verify_schnorr` still succeeds for input 0 with the new tx (proving the signature is still valid despite output/OP_RETURN change).
5. Broadcast attacker's tx, `generatetoaddress` one block, and assert via `getrawmempool`/block scan that the attacker's txid (not the honest operator's) is the one confirmed spending the withdrawal UTXO.
6. Run the verifier's `update_finalized_payouts` equivalent path (or call `get_payout_txs_for_withdrawal_utxos` + parse OP_RETURN) and assert `payout_payer_operator_xonly_pk == operator_B_xonly_pk`, NOT `operator_A_xonly_pk`.
7. Call `Operator::validate_payer_is_operator` as operator A for this deposit_id and assert it returns `Err("Payer is not own operator for deposit")`, proving A is permanently locked out of reimbursement despite having genuinely intended/attempted to fund the withdrawal.

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

**File:** core/src/operator.rs (L620-673)
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
```

**File:** core/src/operator.rs (L1686-1739)
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
```

**File:** core/src/verifier.rs (L1857-1914)
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
```

**File:** core/src/verifier.rs (L2283-2353)
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
    }
```
