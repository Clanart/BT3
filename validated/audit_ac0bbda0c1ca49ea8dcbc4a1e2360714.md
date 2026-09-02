This confirms the full mechanism. `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) determines the payout tx purely by joining on `bitcoin_syncer_spent_utxos.txid/vout` matching the withdrawal UTXO — i.e., whichever transaction actually spends that specific UTXO on-chain is treated as "the" payout tx, with no check that it's the same transaction the operator broadcast. `update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) then parses whichever OP_RETURN is in that confirmed tx to attribute payer identity.

Critically, `parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs:161-187`) enforces the user's signature must use `TapSighashType::SinglePlusAnyoneCanPay`. Under BIP341, `SINGLE|ANYONECANPAY` commits only to the single input being spent and the output at the same index (index 0, the user payout) — it does **not** commit to any other outputs. `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the tx with output 0 (user payout), output 1 (anchor), and output 2 (OP_RETURN with the operator's xonly_pk) — outputs 1 and 2 are unsigned by the user's key-path signature.### Title
Payer attribution for a fronted payout is taken from the unsigned OP_RETURN output, letting anyone re-tag a mined payout to a different operator - ([File: core/src/verifier.rs])

### Summary
The withdrawal signature the user provides must use `TapSighashType::SinglePlusAnyoneCanPay` (enforced in `core/src/rpc/parser/operator.rs:182`), which under BIP341 commits only to input 0 and output 0 - not to the anchor output or the OP_RETURN output that carries the operator's xonly-pubkey. Since `update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) derives the credited operator purely by parsing whatever OP_RETURN happens to be in the transaction that ends up confirmed for that withdrawal UTXO (matched only by `bitcoin_syncer_spent_utxos.txid/vout`, see `get_payout_txs_for_withdrawal_utxos` in `core/src/database/verifier.rs:170-196`), anyone can rebroadcast a variant of the honest payout tx with the OP_RETURN swapped to a different operator's key and the same signature remains valid.

### Finding Description
Binding claimed to hold: `payer_xonly_pk` recorded in `withdrawals.payout_payer_operator_xonly_pk` for withdrawal `i` == the xonly-pk of the operator whose BTC actually funded output 0 of the confirmed payout transaction for withdrawal `i`.

Code path:
1. Operator P calls `Operator::withdraw` (`core/src/operator.rs:560-637`), builds `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) with output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(P's xonly_pk), and sets witness `set_p2tr_key_spend_witness(&user_sig, 0)`.
2. The user's `in_signature` must have `sighash_type == TapSighashType::SinglePlusAnyoneCanPay`, enforced by `parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs:174-187`) and re-verified in `Operator::withdraw` at `core/src/operator.rs:630-637`. Per BIP341, `SinglePlusAnyoneCanPay` signs only the spent input and the output at the same index (index 0); outputs 1 (anchor) and 2 (OP_RETURN) are **not covered** by the signature.
3. An attacker (unprivileged, permitted to broadcast Bitcoin transactions and fee-bump/replace mempool transactions) takes P's broadcast payout tx, copies input 0 and output 0 verbatim (keeping the valid, unmodified `user_sig` witness), and swaps output 2's OP_RETURN to real operator Q's xonly_pk (and/or the anchor output), then gets this variant confirmed instead of P's original (e.g., via a higher-fee conflicting spend of the same withdrawal input, since P's payout tx is non-standard version `NON_STANDARD_V3` sent through TxSender/RBF machinery, or simply beating P's broadcast to the mempool).
4. On the confirmed block, `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) looks up which txid spent the withdrawal UTXO via `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) - this only matches on the previously-committed `withdrawal_utxo_txid/vout`, with no check that the confirmed tx is byte-identical to what P constructed/signed off-chain for attribution purposes. It parses the OP_RETURN of *whatever* tx is confirmed and stores Q as `payout_payer_operator_xonly_pk`.
5. Operator Q's own `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) polls `get_first_unhandled_payout_by_operator_xonly_pk(Q)` (`core/src/database/verifier.rs:282-313`), finds the withdrawal, calls `Operator::handle_finalized_payout`, and marks it handled - queuing Q up to eventually claim `create_reimburse_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:341-385`) for a payout it never funded.
6. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) only compares the DB-recorded `operator_xonly_pk` (which is now Q, due to the attack) against `kickoff_data.operator_xonly_pk`; since these now match for Q, Q's kickoff is judged non-malicious. Meanwhile P, who actually paid the user, has no DB record naming P as payer for that withdrawal and can never pass `validate_payer_is_operator` (`core/src/operator.rs:1687-1740`), permanently losing the ability to claim reimbursement (`get_payout_info_from_move_txid` / `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` will show Q, not P).

No existing guard prevents this: `SECP.verify_schnorr` in `withdraw` only checks the user's signature against the *sighash the operator itself computed* at broadcast time - it does not re-verify anything after confirmation, and the sighash type by design (documented in the proto comment at `core/src/rpc/clementine.proto:242-243`) excludes the OP_RETURN from commitment. `is_kickoff_malicious` trusts the DB-recorded attribution unconditionally.

### Impact Explanation
Real operator Q, who fronted nothing, becomes eligible to claim the `Reimburse` transaction and take BTC out of the round/move-to-vault flow that rightfully belongs to operator P who paid the withdrawing user. Operator P is left having paid the user out of pocket while permanently unable to prove payer attribution for that withdrawal (fails `validate_payer_is_operator`), amounting to an honest operator being reimbursed for a payout it never funded (Critical) and a matching honest operator becoming permanently unable to be reimbursed. This is repeatable per withdrawal - any payout tx broadcast under `SinglePlusAnyoneCanPay` is subject to this OP_RETURN-swap race, across any deposit/withdrawal pair and against any set of live operators.

### Likelihood Explanation
Preconditions: an honest operator P must broadcast a payout tx (standard flow, happens on every withdrawal); a second real operator Q must be running `PayoutCheckerTask`. The attacker only needs to observe P's payout in the mempool, construct a trivial transaction reusing input 0 + output 0 + the same valid witness, substitute the OP_RETURN, and win the confirmation race (fee-bump/RBF or simple broadcast race) - no keys, no privileged role, and no Bitcoin value beyond ordinary fees are required. This is a low-cost, mempool-level manipulation exploiting the intentional `SinglePlusAnyoneCanPay` design choice, making it highly feasible and repeatable for every withdrawal processed by the bridge.

### Recommendation
Do not rely solely on the mined transaction's OP_RETURN (an unsigned, malleable field under `SinglePlusAnyoneCanPay`) to attribute payer identity. Options: (a) have the operator commit to its own xonly_pk inside data that the signature does cover (e.g., require the withdrawal signature to sign an output containing operator attribution, or embed the operator identity in something the user signs off-chain via the aggregator/verification-signature flow rather than reading it from an unauthenticated on-chain field), and/or (b) when detecting the confirmed payout tx, require it to exactly match a specific pre-registered candidate transaction (e.g., match on full serialized transaction hash pre-recorded by the operator when calling `withdraw`, not just input/output-index matching), rejecting any confirmed transaction that isn't the one the attributed operator actually constructed and broadcast.

### Proof of Concept
```
cargo test --package core --lib -- database::verifier::tests::update_get_payout_txs_from_citrea_withdrawal --exact
```
Extend with a new test in `core/src/database/verifier.rs` tests module (or `core/src/verifier.rs`) that:
1. Creates a withdrawal UTXO and registers it via `upsert_move_to_vault_txid_from_citrea_deposit` / `update_withdrawal_utxo_from_citrea_withdrawal`.
2. Simulates P's honest payout tx: build the tx with `create_payout_txhandler` using operator P's xonly_pk and a `SinglePlusAnyoneCanPay` signature from a test user key (as in `sign_withdrawal_output`, `core/src/test/common/setup_utils.rs:499-543`).
3. Constructs the "attacker variant": same input, same output 0 and same witness, but OP_RETURN swapped to operator Q's xonly_pk; register this txid (not P's) as the one spending the withdrawal UTXO via `insert_spent_utxo`.
4. Calls `update_payout_txs_and_payer_operator_xonly_pk` / the code path equivalent to `update_finalized_payouts` parsing this attacker tx, and asserts `get_payout_info_from_move_txid` returns payer xonly_pk == Q (not P).
5. Asserts `get_first_unhandled_payout_by_operator_xonly_pk(Q)` returns the withdrawal, while calling it for P returns `None`.
6. Asserts `Verifier::is_kickoff_malicious` returns `false` for a kickoff constructed with `kickoff_data.operator_xonly_pk == Q`, and (conceptually, via `validate_payer_is_operator`) that P's own `Operator::get_reimbursement_txs`/`validate_payer_is_operator` call for the same deposit_id errors with "Payer is not own operator for deposit." [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/verifier.rs (L1859-1915)
```rust
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

**File:** core/src/operator.rs (L1686-1729)
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
```
