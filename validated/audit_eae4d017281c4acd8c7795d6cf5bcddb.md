### Title
Payout attribution is spoofable via unsigned OP_RETURN data - allows attacker-funded payout to be falsely credited to any operator (File: core/src/verifier.rs)

### Summary
`update_finalized_payouts` in `core/src/verifier.rs` derives `payout_payer_operator_xonly_pk` solely by parsing the OP_RETURN output of the confirmed payout transaction, with no cryptographic binding between that pubkey and the actual source of the funds in the transaction. Because the withdrawal UTXO is spent with a `SinglePlusAnyoneCanPay` signature that only commits to input 0 and output 0, any unprivileged attacker can add extra inputs funded entirely from their own wallet and stamp an arbitrary (real) operator's xonly pubkey into the OP_RETURN, causing the bridge's DB and downstream kickoff/reimbursement logic to record that operator as having fronted a payout it never paid for.

### Finding Description
The broken binding is:
`value_fronted_by(operator_xonly_pk) == value_recorded_as_fronted(operator_xonly_pk)`

Trace:
1. The payout tx template is `create_payout_txhandler` [1](#0-0) , which only key-spend-signs input 0 (the withdrawal UTXO) with the user's `SinglePlusAnyoneCanPay` signature; the OP_RETURN output (`operator_xonly_pk.serialize()`) and any additional funding inputs/outputs are completely unsigned/uncommitted by that signature.
2. An attacker who calls Citrea's `withdraw` supplies `in_signature`, `in_outpoint`, `output_script_pubkey`, `output_amount` themselves (per the threat model), so they fully control a withdrawal request and its `SinglePlusAnyoneCanPay` signature - this is exactly the signature verified in `Operator::withdraw` / RPC `withdraw` [2](#0-1) , but nothing prevents the attacker from building and broadcasting their **own** transaction directly to Bitcoin (bypassing the operator entirely) that spends the same withdrawal UTXO as input 0, adds extra attacker-funded inputs, pays output 0 to themselves/user, and appends an OP_RETURN containing any real operator's xonly pubkey.
3. When this transaction confirms, the verifier's block sync calls `update_finalized_payouts`, which extracts the OP_RETURN xonly pubkey with no ownership/signature check and writes it as `payout_payer_operator_xonly_pk` [3](#0-2) , persisted via `update_payout_txs_and_payer_operator_xonly_pk` [4](#0-3) .
4. The named operator's own `PayoutCheckerTask` later reads this record via `get_first_unhandled_payout_by_operator_xonly_pk` and automatically proceeds to kickoff/reimbursement (`handle_finalized_payout`) [5](#0-4) , and `validate_payer_is_operator` only checks that the DB's recorded pubkey equals the operator's own key - it never checks that any of the tx's value actually originated from the operator's wallet [6](#0-5) .
5. `Verifier::is_kickoff_malicious`, which validates a kickoff against payout data, similarly only compares the OP_RETURN-derived pubkey against the kickoff's committed operator, which is circular since the OP_RETURN is exactly the attacker-controlled field [7](#0-6) .

None of the existing guards (`SECP.verify_schnorr` on the user's SIGHASH input, `is_kickoff_malicious`, `validate_payer_is_operator`) verify that the operator whose pubkey appears in OP_RETURN actually supplied any of the additional inputs/value in the payout transaction. The attribution is a pure unauthenticated data field.

### Impact Explanation
This breaks the invariant that reimbursement is only paid to the operator who fronted the corresponding withdrawal. A named operator can be credited (and can automatically claim, via its own automated `PayoutCheckerTask`/kickoff flow) a Reimburse payout for a withdrawal it never funded, i.e., bridge/round collateral value is released to an operator without a matching real fronted payment - directly matching the Critical category "an operator reimbursed for a payout it never funded." The attack is repeatable for every withdrawal and against any operator whose public xonly key is known (public information), since nothing operator-specific or secret is required to forge the OP_RETURN attribution.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to control a withdrawal request (calling Citrea's `withdraw`) and be able to broadcast a Bitcoin transaction spending the resulting withdrawal UTXO with extra self-funded inputs and a chosen OP_RETURN - all explicitly within the unprivileged attacker's stated capabilities. Cost is just Bitcoin fees plus the withdrawal amount, which the attacker pays to themselves/a controlled address, so net cost is close to zero (fees only). No majority hashrate, key compromise, or verifier/operator/aggregator privilege is required. This is feasible on every honest operator running default automation.

### Recommendation
Do not derive payout attribution from unauthenticated OP_RETURN data alone. Require the payout transaction's OP_RETURN commitment (or an equivalent binding) to be covered by a signature/commitment that also authenticates that the additional funding inputs belong to (or were authorized by) the claimed operator - e.g., require the operator to sign over the full payout transaction (all inputs) with a key linked to their registered collateral/round, or bind reimbursement eligibility to verifiable proof that the operator's own UTXO(s) funded the payout output, rather than trusting arbitrary OP_RETURN bytes.

### Proof of Concept
```rust
// cargo test plan (core/src/database/verifier.rs test module or a new integration test)
// 1. Set up test DB and register a withdrawal UTXO for `index`.
// 2. Construct a "victim" operator's xonly_pk (`operator_xonly_pk`) WITHOUT giving it
//    any wallet/UTXO involvement.
// 3. Build a payout tx spending the withdrawal UTXO as input 0 with a
//    SinglePlusAnyoneCanPay signature covering only input/output 0, then add an
//    extra input from a separate "attacker" wallet/address (not operator's) to
//    cover the output value + fees, and append an OP_RETURN with
//    `operator_xonly_pk.serialize()`.
// 4. Feed this tx through `update_finalized_payouts` (or directly call
//    `update_payout_txs_and_payer_operator_xonly_pk` with the parsed OP_RETURN pubkey,
//    mirroring production parsing logic).
// 5. Assert:
//    let (payer_pk, ..) = db.get_payout_info_from_move_txid(...).await.unwrap().unwrap();
//    assert_eq!(payer_pk, Some(operator_xonly_pk)); // attribution recorded
// 6. Independently assert the "operator" wallet balance/UTXO set used to fund the
//    extra inputs is untouched (i.e., all extra inputs trace to the attacker's keys,
//    none to operator_xonly_pk's controlled outputs), proving
//    value_fronted_by(operator) == 0 while value_recorded_as_fronted(operator) == output_txout.value.
```

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

**File:** core/src/verifier.rs (L2312-2350)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
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

**File:** core/src/task/payout_checker.rs (L39-106)
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
```
