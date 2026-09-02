## Finding Analysis

The binding being questioned is: **`move_txid` returned by `get_first_unhandled_payout_by_operator_xonly_pk` == the `move_txid` whose withdrawal the querying operator actually fronted with its own funds.**

Tracing the code shows this binding is broken, but not exactly via the "two competing OP_RETURNs / idx race" mechanism framed in the question — it's broken at a deeper level: the `payout_payer_operator_xonly_pk` column itself can be forged by anyone, independent of who actually funded the payout.

### Root cause trace

- `create_payout_txhandler` builds the payout tx with the withdrawer's `user_sig` covering only input 0 / output 0 via `set_p2tr_key_spend_witness`, and the OP_RETURN (operator attribution) as a separate, unsigned-by-that-key output. [1](#0-0) 
- The withdrawer's signature is explicitly produced with `TapSighashType::SinglePlusAnyoneCanPay`, which (per BIP341) commits only to the single input being spent and its paired output — it does **not** cover the anchor output or the OP_RETURN operator-attribution output. [2](#0-1) 
- `update_finalized_payouts` blindly parses whichever OP_RETURN xonly-pubkey appears in the confirmed payout tx and stores it as `payout_payer_operator_xonly_pk`, with no check that the named operator's own key/funds were used anywhere in the transaction's inputs. [3](#0-2) 
- `get_first_unhandled_payout_by_operator_xonly_pk` trusts this column purely by string/key equality and ordering by `idx`, with no cryptographic link back to actual fund provenance. [4](#0-3) 
- `PayoutCheckerTask::run_once` takes whatever `move_to_vault_txid` this returns and drives `handle_finalized_payout`/kickoff for that deposit. [5](#0-4) 
- The verifier-side guard `is_kickoff_malicious` re-derives its "truth" from the exact same untrusted `payout_payer_operator_xonly_pk` column via `get_payout_info_from_move_txid`, so it will match by construction and not flag the kickoff as malicious. [6](#0-5) 

### Exploit flow

Since the withdrawer's `SinglePlusAnyoneCanPay` signature never authenticates the OP_RETURN, and any additional funding inputs needed to reach the payout amount are added later without being covered by that signature either, an attacker who is simultaneously the withdrawer (owns the dust UTXO and its signature — which is directly attacker-suppliable per the threat model, e.g., via `WithdrawParams.input_signature`) can independently construct and broadcast a fully-funded, valid payout transaction for their own withdrawal, but insert an arbitrary (victim) operator's xonly-pubkey into the OP_RETURN instead of their own. No operator RPC path is required for this — it's pure Bitcoin transaction construction using publicly-broadcastable data (the withdraw params are sent to all operators via the aggregator, and the attacker as withdrawer already possesses the signature).

Once mined and finality-synced, `update_finalized_payouts` records the victim operator's pubkey against this attacker-funded withdrawal row. If this row's `idx` is smaller than the victim operator's own legitimately-fronted withdrawal, `get_first_unhandled_payout_by_operator_xonly_pk` will return it first, causing the victim operator to issue a kickoff/reimbursement for a deposit it never fronted. `is_kickoff_malicious` will not catch this because it checks the same forged DB column, not actual fund provenance.

### Title
Unauthenticated OP_RETURN operator attribution in payout tx lets anyone forge `payout_payer_operator_xonly_pk`, causing wrong-operator reimbursement - (File: core/src/verifier.rs, core/src/database/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The payout transaction's OP_RETURN output (which names the operator entitled to reimbursement) is not covered by the withdrawer's `SinglePlusAnyoneCanPay` signature, and `update_finalized_payouts` records this OP_RETURN value into `withdrawals.payout_payer_operator_xonly_pk` with no verification that the named operator actually supplied the payout funds. `get_first_unhandled_payout_by_operator_xonly_pk` and `is_kickoff_malicious` both trust this column blindly, so an attacker can front their own withdrawal but attribute it to an arbitrary operator, hijacking that operator's reimbursement flow.

### Finding Description
See trace above. The broken equality: `move_txid` returned to an operator by `get_first_unhandled_payout_by_operator_xonly_pk` is assumed to equal the `move_txid` of a withdrawal that operator itself fronted, but the only thing tying a `move_txid` row to an `operator_xonly_pk` is an unauthenticated OP_RETURN byte string in a transaction the operator may never have constructed or funded, since the withdrawer's SIGHASH_SINGLE|ANYONECANPAY signature does not cover that output.

### Impact Explanation
Critical — an operator can be reimbursed (round collateral released / reimburse-connector claimed) for a payout it never funded, while the real depositor/withdrawer keeps their own funds (self-paid). This is repeatable per withdrawal and does not require privileged access, majority hashrate, or key compromise — only capital equal to the bridge amount (returned to the attacker themselves) and normal Bitcoin broadcast capability.

### Likelihood Explanation
Requires only unprivileged capabilities explicitly granted to the attacker (choose withdrawal UTXO, its signature/sighash flag, broadcast a Bitcoin transaction). Cost is temporary capital equal to `bridge_amount` (returned to the attacker's own withdrawal address), plus fees. No collusion, no verifier/operator compromise needed.

### Recommendation
Cryptographically bind the payout's operator attribution to the actual value-providing inputs, e.g., require the additional funding input(s) to be signed by (or otherwise provably controlled by) the named `operator_xonly_pk`, or have `update_finalized_payouts`/`is_kickoff_malicious` verify that the funding UTXOs of the payout transaction trace back to the named operator's known wallet/collateral, rather than trusting an unauthenticated OP_RETURN byte string.

### Proof of Concept
`cargo test` in `core/src/database/verifier.rs` tests module or `core/src/test/deposit_and_withdraw_e2e.rs`: construct a withdrawal, have the "attacker" (not any registered operator) complete and broadcast the payout transaction with an OP_RETURN naming operator B's xonly pubkey while funding all inputs itself; sync finalized blocks; assert `get_payout_info_from_move_txid` and `get_first_unhandled_payout_by_operator_xonly_pk(operator_B_pk)` return this move_txid; then assert `is_kickoff_malicious` for operator B's synthetic kickoff on this deposit returns `false` despite operator B having contributed no funds to the payout transaction's inputs.

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

**File:** core/src/test/common/setup_utils.rs (L499-543)
```rust
fn sign_withdrawal_output(
    config: &BridgeConfig,
    dust_utxo: &UTXO,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (bitcoin::TxOut, taproot::Signature) {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);
    let txin = builder::transaction::input::SpendableTxIn::new(
        dust_utxo.outpoint,
        dust_utxo.txout.clone(),
        vec![],
        None,
    );
    let txout = bitcoin::TxOut {
        value: withdrawal_amount,
        script_pubkey: withdrawal_address.script_pubkey(),
    };
    let unspent_txout = builder::transaction::output::UnspentTxOut::from_partial(txout.clone());

    let tx = builder::transaction::TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            builder::transaction::DEFAULT_SEQUENCE,
        )
        .add_output(unspent_txout.clone())
        .finalize();

    let sighash = tx
        .calculate_sighash_txin(0, sighash::TapSighashType::SinglePlusAnyoneCanPay)
        .expect("Failed to calculate sighash");

    let sig = signer
        .sign_with_tweak_data(sighash, builder::sighash::TapTweakData::KeyPath(None), None)
        .expect("Failed to sign");

    let sig = taproot::Signature {
        signature: sig,
        sighash_type: sighash::TapSighashType::SinglePlusAnyoneCanPay,
    };

    (txout, sig)
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
