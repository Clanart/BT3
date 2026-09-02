## Analysis

**Binding claimed to hold:** `payout_payer_operator_xonly_pk` (stored in the `withdrawals` DB row, read by `get_first_unhandled_payout_by_operator_xonly_pk`) should equal "the operator that actually fronted this specific withdrawal with its own funds." I traced this binding end-to-end and found it is **never cryptographically enforced** — it is set from unauthenticated OP_RETURN data in the payout Bitcoin transaction, which anyone broadcasting a transaction can write.

**Root cause path:**

1. `update_finalized_payouts` extracts the operator attribution purely from the first OP_RETURN output of the payout transaction with no signature check: [1](#0-0) 
2. `create_payout_txhandler` — the reference implementation used by a legitimate operator — spends the withdrawal UTXO with **only the user's signature** (`SpendPath::KeySpend`, witness = `user_sig`); the OP_RETURN containing `operator_xonly_pk` is plain, unsigned pushdata, not committed to by any signature: [2](#0-1) . Since spending only requires the withdrawal UTXO owner's signature, **anyone who owns that UTXO can build and broadcast an equivalent transaction themselves**, embedding an arbitrary operator's xonly pubkey.
3. `Operator::withdraw` confirms the only real constraint on the input is that it matches the registered Citrea withdrawal UTXO and carries a valid Schnorr signature from that UTXO's owner — no operator identity binding exists on-chain: [3](#0-2) 
4. The forged attribution is persisted verbatim into `withdrawals.payout_payer_operator_xonly_pk`: [4](#0-3) 
5. `PayoutCheckerTask` for operator X polls exactly by that column filtered on X's own key, with no further verification that X actually broadcast anything: [5](#0-4) , backed by [6](#0-5) 
6. `send_asserts` only checks that the (forged) DB value equals `kickoff_data.operator_xonly_pk` — which trivially holds since the forged value *is* X's key — then proceeds to build the full kickoff/assert chain to claim reimbursement: [7](#0-6) 
7. The verifier-side sanity check `is_kickoff_malicious`, which exists precisely to catch operator/payout mismatches, reads the *same* poisoned DB column and therefore also passes, since `operator_xonly_pk == kickoff_data.operator_xonly_pk` by construction of the attack: [8](#0-7) 

**Exploit flow:** An attacker (a) calls `withdraw()` on the Citrea Bridge contract choosing an `input_outpoint` they themselves control the private key for and an output script/amount of their choosing, referencing *any* existing unclaimed deposit index; (b) instead of contacting any operator's gRPC `Withdraw`/`InternalWithdraw` endpoint, they directly broadcast to Bitcoin their own transaction spending that `input_outpoint` with their own valid Schnorr signature, an output paying themselves, and an OP_RETURN containing victim operator X's public xonly pubkey (learned via the public `fetch_operator_keys`/gRPC surface). No operator or verifier key, cooperation, or signature is required for any of this. Once Bitcoin sync/Citrea sync ingest the tx, X's own automation (`PayoutCheckerTask` → `send_asserts` → kickoff/assert/reimburse graph) will treat this as if X had fronted the payout and drive a full reimbursement claim against the underlying deposit's collateral, paying X the `bridge_amount` for a payout X never made and never funded — while the withdrawing user has already been paid separately by the attacker's own self-funded transaction. Existing guards (`is_kickoff_malicious`, `SECP.verify_schnorr` on the *user's* signature, storage-proof/SPV checks) validate only that *a* payout transaction with the right shape exists and was correctly signed by the withdrawal-UTXO owner — none of them require that the OP_RETURN operator identity is authenticated by that operator.

### Title
Unauthenticated OP_RETURN operator attribution lets an attacker falsely credit any operator's reimbursement automation for a payout it never funded - (File: core/src/verifier.rs / core/src/database/verifier.rs / core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `payout_payer_operator_xonly_pk` column, which drives an operator's automated reimbursement flow, is populated straight from an unsigned OP_RETURN pushdata in the payout Bitcoin transaction with no cryptographic link to the named operator. Any unprivileged party who owns the withdrawal UTXO (a normal Citrea withdrawer) can self-construct and broadcast a valid payout transaction naming an arbitrary victim operator, causing that operator's automation to claim bridge-funded reimbursement for a front it never made.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk == the operator that actually fronted this withdrawal`. In reality this column is set purely from `parse_op_return_data` on the payout tx (`update_finalized_payouts`, `core/src/verifier.rs:2312-2321`), and the payout transaction itself (`create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`) requires only the withdrawal-UTXO owner's Schnorr signature — no signature from, or commitment by, the named operator. The withdrawal UTXO and its private key are entirely attacker-chosen (per the ruleset, the attacker may choose the withdrawal UTXO bytes/signature and call Citrea's `withdraw`). The attacker therefore broadcasts a self-funded, self-signed payout transaction that pays themself, with an OP_RETURN naming victim operator X. Downstream, `get_first_unhandled_payout_by_operator_xonly_pk` (`core/src/database/verifier.rs:282-313`) and `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-60`) pick this up for X purely by matching X's own key against the poisoned column, and `Operator::send_asserts` (`core/src/operator.rs:1275-1296`) proceeds to build the kickoff/assert chain believing X fronted the payout. The verifier-side guard `is_kickoff_malicious` (`core/src/verifier.rs:1857-1914`) cannot catch this because it compares `kickoff_data.operator_xonly_pk` against the same forged DB column it was meant to police.

### Impact Explanation
The underlying deposit's move-to-vault collateral (`bridge_amount`) is ultimately released via the Reimburse transaction to operator X, who never fronted the withdrawal — matching "an operator reimbursed for a payout it never funded" and, since the deposit's collateral leaves the bridge with no genuine matching front by X, also "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." This is repeatable across every withdrawal and every operator (attacker picks any known operator xonly pubkey), and requires no operator, verifier, or aggregator collusion. Blast radius: system-wide, any deposit that has an available/unclaimed withdrawal slot can be targeted.

### Likelihood Explanation
Preconditions are minimal: attacker needs only Bitcoin fee funds and (transiently) BTC roughly equal to the withdrawal amount to self-pay themselves (returned to them net of fees), plus the ability to call Citrea's public `withdraw()` and broadcast a Bitcoin transaction — both explicitly within the unprivileged attacker's granted capabilities. No key compromise, majority hashrate, or social engineering is needed. The attack is fully deterministic and repeatable.

### Recommendation
Bind the operator attribution cryptographically to the payout transaction, e.g. require the operator's Schnorr signature (or a MuSig2 partial signature already known to verifiers) over the OP_RETURN payload/operator pubkey, or require that the payout transaction's fee-bumping/anchor input traces back to a UTXO under the named operator's own wallet, and validate that binding in `update_finalized_payouts`/`is_kickoff_malicious` before treating a payout as "fronted by X."

### Proof of Concept
```
cargo test forged_op_return_attribution -- --nocapture
```
Plan:
1. Set up a two-operator e2e harness as in `core/src/test/deposit_and_withdraw_e2e.rs`; perform a normal deposit so a move-to-vault UTXO with `bridge_amount` exists.
2. Register a withdrawal on the mocked Citrea client (`citrea_client.collect_withdrawal_utxos`) using an outpoint whose private key is controlled by the test/attacker identity (not operator 0 or 1).
3. Directly build a `Transaction` (bypassing `Operator::

### Citations

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

**File:** core/src/operator.rs (L588-637)
```rust
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

**File:** core/src/task/payout_checker.rs (L39-60)
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
```
