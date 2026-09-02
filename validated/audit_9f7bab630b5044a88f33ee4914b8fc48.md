### Title
Payout OP_RETURN operator attribution is unsigned by the withdrawal signature, letting anyone broadcast a payout and frame a different operator as the payer - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The payout transaction that fronts a user withdrawal is only partially covered by the user's signature. The `SinglePlusAnyoneCanPay` sighash the protocol relies on binds just the spent input and the output at the same index (the user payout output), while the second output — the `OP_RETURN` that records *which operator* fronted the withdrawal — is left completely unsigned and can be freely substituted by anyone who possesses the withdrawal signature and outpoint (both of which travel through the operator RPC / Citrea withdrawal bookkeeping, not the same "operator private key" secret). The bridge's later accounting (`update_finalized_payouts`, `is_kickoff_malicious`, `validate_payer_is_operator`) trusts this OP_RETURN value verbatim as "the operator who paid," breaking the binding: operator credited == operator that actually paid.

### Finding Description
`create_payout_txhandler` builds the payout tx with three outputs: the user payout output (index 0), an anchor (index 1), and an `op_return_txout` embedding whichever `operator_xonly_pk` is passed in (index 2): [1](#0-0) 

The single taproot key-spend input is signed with `user_sig`, whose sighash type is meant to be `SinglePlusAnyoneCanPay` (documented in the proto and enforced only by the digest computed from whatever `sighash_type` is embedded in the caller-supplied signature): [2](#0-1) [3](#0-2) 

`SIGHASH_SINGLE | ANYONECANPAY` commits only to the single spent input and the output at the matching index (index 0 — the user's payout output). It does **not** commit to output index 1 (anchor) or output index 2 (the `OP_RETURN` carrying `operator_xonly_pk`). Consequently, any party who can obtain the user's signature and the withdrawal outpoint (which are not additionally bound to a specific operator identity by that signature) can construct their own payout transaction that pays the exact same output the user signed for, but stamps an arbitrary `operator_xonly_pk` — e.g., a different, uninvolved operator — into the `OP_RETURN`, fund it with their own extra input(s) (permitted by `ANYONECANPAY`), and broadcast it.

Downstream, the verifier blindly trusts this OP_RETURN as ground truth for "who paid":
- `update_finalized_payouts` parses the OP_RETURN from the confirmed payout tx and records `payout_payer_operator_xonly_pk` in the `withdrawals` table without any further attribution proof: [4](#0-3) [5](#0-4) 

- This value is then used to authorize an operator's own reimbursement pipeline: `validate_payer_is_operator` checks only that the recorded payer equals the local operator's key, and `get_first_unhandled_payout_by_operator_xonly_pk` queues it for that operator's automated kickoff/reimbursement flow: [6](#0-5) [7](#0-6) 

- `is_kickoff_malicious` similarly trusts `operator_xonly_pk` from the DB (sourced from the unauthenticated OP_RETURN) to decide whether a kickoff from that operator is legitimate: [8](#0-7) 

Because the actual value transferred to the user (output 0) is the only thing cryptographically bound by the signature, whoever funds the extra input and broadcasts the transaction is the true payer, but the protocol's sole source of truth for "who fronted this payout and is owed reimbursement" is the unauthenticated `OP_RETURN`, which the broadcaster fully controls. This breaks the "operator credited == party that paid" invariant: an operator's identity can be stamped onto a payout it never funded, letting that operator's (or that operator's queue's) automated reimbursement flow attempt to draw funds for a payout it did not make.

### Impact Explanation
This matches the Critical class "an operator reimbursed for a payout it never funded": the DB attribution that gates `get_reimbursement_txs`/kickoff automation for a given operator is derived entirely from data (input signature + outpoint) that is not bound to operator identity, and from an OP_RETURN output that is not covered by any signature at all. Any unprivileged party that intercepts or otherwise obtains the withdrawal signature/outpoint (which are transmitted over the withdrawal RPC path and are not a secret unique to a specific operator) can front the payout with their own funds while attributing it to an arbitrary operator xonly-pk, causing:
- the credited operator's node to treat an un-funded-by-them payout as its own and proceed through the reimbursement pipeline (Round → Kickoff → Reimburse), eventually spending the bridge's `MoveToVault` funds via `create_reimburse_txhandler` toward that operator, without that operator having fronted anything.
- alternately, if attribution targets an operator that never runs the kickoff (e.g., a decommissioned or targeted honest operator), the deposit's reimbursement bookkeeping becomes permanently stuck to a payer identity that will never claim it, which can lock the withdrawal's reimbursement path.

### Likelihood Explanation
Likelihood is high given reachability: the withdrawal signature and outpoint are exchanged through the standard (non-operator-secret) withdrawal flow, the `ANYONECANPAY|SINGLE` sighash is exactly the scheme the protocol documents and expects, and no code anywhere re-verifies that the broadcaster of the confirmed payout transaction is the same entity named in its own `OP_RETURN`. The exploit only requires constructing a standard Bitcoin transaction and broadcasting it — no verifier, operator, or aggregator role is needed.

### Recommendation
Bind the operator identity output to the same signature that authorizes the spend, e.g., by using `SIGHASH_ALL` (or `SIGHASH_ALL|ANYONECANPAY`) so all outputs including the `OP_RETURN` are committed, or by having the user pre-commit to (or separately sign) the specific operator's pubkey allowed to claim the reimbursement for that withdrawal, and validating that commitment when recording `payout_payer_operator_xonly_pk` during `update_finalized_payouts`.

### Proof of Concept
1. Obtain a valid withdrawal signature (`sig`), `input_outpoint`, `output_script_pubkey`, and `output_amount` for a pending Citrea withdrawal (transmitted for use with the `Withdraw`/`InternalWithdraw` RPC path, as constructed in [9](#0-8) ).
2. Independently construct a payout transaction identical to what `create_payout_txhandler` would build for output 0 (the exact user output committed by `SIGHASH_SINGLE|ANYONECANPAY`), but set the `OP_RETURN` (output 2) to an arbitrary `operator_xonly_pk` of choice rather than the intended fronting operator's key — reference construction: [1](#0-0) .
3. Add an additional funding input (permitted by `ANYONECANPAY`) from any wallet, sign only that input, and broadcast.
4. Once confirmed, the verifier's `update_finalized_payouts` will parse the forged `OP_RETURN` and record the chosen `operator_xonly_pk` as `payout_payer_operator_xonly_pk` for this withdrawal — [4](#0-3)  — even though that operator never funded the payout, after which that operator's automated pipeline (`validate_payer_is_operator` / `get_first_unhandled_payout_by_operator_xonly_pk`) will treat it as its own reimbursable payout.

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

**File:** core/src/rpc/clementine.proto (L239-253)
```text
message WithdrawParams {
  // The ID of the withdrawal in Citrea
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
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

**File:** core/src/verifier.rs (L1857-1890)
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
```

**File:** core/src/verifier.rs (L2312-2335)
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

**File:** core/src/test/common/clementine_utils.rs (L42-53)
```rust
) -> OutPoint {
    let withdrawal_params = WithdrawParams {
        withdrawal_id,
        input_signature: sig.serialize().to_vec(),
        input_outpoint: Some((*withdrawal_utxo).into()),
        output_script_pubkey: payout_txout.script_pubkey.to_bytes(),
        output_amount: payout_txout.value.to_sat(),
    };
    let verification_signature = sign_withdrawal_verification_signature::<OperatorWithdrawalMessage>(
        &e2e.config,
        withdrawal_params.clone(),
    );
```
