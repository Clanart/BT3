### Title
Payout attribution (operator OP_RETURN) is not covered by the user's withdrawal signature, allowing reimbursement misattribution via replace-by-fee - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `create_payout_txhandler` function builds the payout transaction with a single signed input (the user's dust withdrawal UTXO, signed `SinglePlusAnyoneCanPay`) and three outputs: the user's payout, an anchor, and an `op_return_txout` that records which operator's `xonly_pk` fronted the withdrawal. Because `SIGHASH_SINGLE | ANYONECANPAY` only commits the signature to the input's corresponding output (index 0) and to no other inputs, neither the OP_RETURN output (attribution) nor any additional funding inputs are covered by the user's signature. Anyone who observes the unconfirmed payout transaction in the mempool can therefore construct a fee-bumping replacement that spends the same signed input, pays the exact same committed output to the user, but substitutes their own funding inputs and an arbitrary (or corrupted/absent) operator attribution in the OP_RETURN output.

### Finding Description
`create_payout_txhandler` [1](#0-0)  builds:
- Input 0: the withdrawal UTXO, spent via `SpendPath::KeySpend` with the user's signature.
- Output 0: user payout (`output_txout`).
- Output 1: anchor output.
- Output 2: `op_return_txout` containing the fronting operator's `xonly_pk`.

`Operator::withdraw` verifies the user's signature strictly against `sighash_txin(0, in_signature.sighash_type)` and requires it to be `SinglePlusAnyoneCanPay` [2](#0-1) . Under BIP-341/BIP-118 semantics, `SIGHASH_SINGLE|ANYONECANPAY` binds the signature only to the corresponding output (index 0) — it does not commit to output 1 (anchor), output 2 (OP_RETURN attribution), or to any other transaction inputs. The transaction is then funded by `fund_raw_transaction`/RBF machinery, which adds the operator's own wallet inputs to cover the gap between the small dust input and the (larger) payout amount [3](#0-2) .

The resulting payer attribution is later read back purely from the OP_RETURN of whichever transaction first spends the withdrawal UTXO on-chain: `update_finalized_payouts` scans the confirming block, extracts the OP_RETURN xonly_pk (or `None` if missing/invalid), and stores it as `payout_payer_operator_xonly_pk` [4](#0-3) . Reimbursement logic (`validate_payer_is_operator`, `PayoutCheckerTask`, `is_kickoff_malicious`) all trust this stored attribution as authoritative for who gets to claim reimbursement via kickoff [5](#0-4) [6](#0-5) [7](#0-6) .

Because the OP_RETURN output and the additional funding inputs are outside the signed message, any unprivileged party who sees an operator's unconfirmed payout transaction in the mempool (input 0's witness, containing the signature, is public once broadcast) can build a competing, higher-fee, RBF-replacing transaction that:
- reuses the same signed input 0 (still valid, since the signature only fixes output 0's script/amount),
- supplies its own funding inputs to cover the payout amount (fronting the withdrawal with the attacker's own BTC),
- sets the OP_RETURN to an arbitrary operator's `xonly_pk`, a bogus key, or omits it entirely.

If it confirms first, the user still receives the exact committed payment (protected by the signature), but the payer attribution recorded by the bridge is now controlled by the attacker rather than reflecting who actually fronted the funds.

### Impact Explanation
This breaks the binding "the operator credited versus the party that paid": the operator whose `xonly_pk` ends up in the OP_RETURN — not necessarily the party who actually supplied the funding inputs — becomes the party entitled to walk the kickoff/reimbursement path (`validate_payer_is_operator`, `PayoutCheckerTask::run_once`, `handle_finalized_payout`) and eventually claim the Reimburse transaction from the bridge, i.e., "an operator reimbursed for a payout it never funded." Conversely, if the attacker sets the OP_RETURN to a bogus/invalid key, `operator_xonly_pk` is recorded as `None` [8](#0-7) , and `get_first_unhandled_payout_by_operator_xonly_pk` — which filters strictly by the querying operator's own key — will never surface this payout to the operator that actually intended to front it (or to any operator), permanently preventing anyone from initiating the kickoff/reimbursement for that withdrawal even though the withdrawal itself confirmed on-chain.

### Likelihood Explanation
Exploitation requires: (1) observing an operator's broadcast-but-unconfirmed payout transaction in the mempool (its witness/signature is public at that point), and (2) being able to fund a competing, higher-fee replacement transaction that fronts the exact committed payout amount to the user out of pocket. This is not free — the attacker must spend real BTC equal to the withdrawal amount to grief/misattribute the reimbursement — which limits the practicality of the attack to griefing rather than direct profit unless the attacker itself controls or colludes with the operator identity placed in the OP_RETURN. It requires no privileged role or key compromise; only mempool visibility and sufficient capital to front the payout.

### Recommendation
Bind the OP_RETURN attribution (and, ideally, the full transaction structure/funding inputs) to the transaction that the user actually signs, or otherwise decouple payer attribution from an easily replaceable, unsigned output. Options: use a sighash type that commits to all outputs (e.g., `SIGHASH_ALL`, if input funding logistics permit), or have the attribution derive from a value that is cryptographically bound to the payout (e.g., include the operator's commitment inside a script-path spend condition or a covenant rather than a bare OP_RETURN), or require the reimbursement flow to independently verify that the additional funding inputs of the confirmed payout transaction were actually spent from the claiming operator's known wallet/keys before honoring attribution.

### Proof of Concept
1. Operator A calls `Operator::withdraw`, producing and broadcasting a payout transaction: input 0 = user's dust UTXO (signed `SinglePlusAnyoneCanPay`), output 0 = user's payout, output 1 = anchor, output 2 = OP_RETURN(A's xonly_pk); additional funding inputs are added by A's wallet via `fund_raw_transaction` [9](#0-8) .
2. This transaction is now visible, unconfirmed, in the mempool; its witness (including the user's signature) is public.
3. Attacker constructs a new transaction reusing input 0 with the same signature and witness, output 0 identical (required by `SIGHASH_SINGLE`), but replaces A's additional funding inputs with attacker-owned inputs covering the same output value, and sets output 2 (OP_RETURN) to a different operator's `xonly_pk` (or a garbage value), with a higher fee rate for RBF priority.
4. Attacker broadcasts and gets this transaction confirmed instead of A's.
5. `update_finalized_payouts` records the attacker-chosen `payout_payer_operator_xonly_pk` (or `None`) from the confirmed transaction's OP_RETURN [10](#0-9) , causing either a different operator than the true funder to be treated as eligible for reimbursement, or no operator at all to ever discover and handle the payout via `PayoutCheckerTask` [11](#0-10) .

Note: I was unable to fully verify the exact `Sequence` value used for the withdrawal input (`DEFAULT_SEQUENCE`) to confirm RBF-signaling in this exact transaction, since the constant's definition body was not retrievable within my remaining tool budget; this is required to fully confirm mempool-replaceability of the specific payout transaction and should be checked directly in `core/src/builder/transaction/txhandler.rs` before treating this as fully confirmed.

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

**File:** core/src/operator.rs (L620-674)
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
            .hex;
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

**File:** core/src/verifier.rs (L2283-2350)
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
```

**File:** core/src/task/payout_checker.rs (L31-79)
```rust
#[async_trait]
impl<C> Task for PayoutCheckerTask<C>
where
    C: CitreaClientT,
{
    type Output = bool;
    const VARIANT: TaskVariant = TaskVariant::PayoutChecker;

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
