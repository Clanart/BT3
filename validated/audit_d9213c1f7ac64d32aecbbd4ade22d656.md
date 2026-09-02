### Title
Payout OP_RETURN operator attribution is not cryptographically bound to the payout tx's funding input, allowing false reimbursement credit - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` writes an arbitrary, caller-supplied `operator_xonly_pk` into the payout transaction's OP_RETURN with no cryptographic link to the key that actually signs/funds the transaction's sole input. `update_finalized_payouts` on the verifier side then blindly trusts this OP_RETURN value as the "payer," and `PayoutCheckerTask`/`handle_finalized_payout` on the operator side use that unverified DB field to trigger real on-chain reimbursement.

### Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk` (recorded from the payout tx's OP_RETURN) == the operator whose signature/funds actually authorized that specific payout tx's input.

Tracing the code shows this binding is never enforced:

- `create_payout_txhandler` builds the payout tx with a single input (`input_utxo`, spent via `SpendPath::KeySpend` using `user_sig`) and appends an OP_RETURN built purely from the function's `operator_xonly_pk` parameter: `op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()))` [1](#0-0) . Nothing ties this bytes value to the key that produced `user_sig` or to any second funding input from that operator.
- On the verifier side, `update_finalized_payouts` parses whatever payout tx actually spent the `withdrawal_utxo` in a finalized block and extracts the OP_RETURN bytes as `operator_xonly_pk`, storing it verbatim as `payout_payer_operator_xonly_pk` [2](#0-1) .
- `get_first_unhandled_payout_by_operator_xonly_pk` selects unhandled payouts purely by matching this DB field against `self.operator.signer.xonly_public_key` [3](#0-2) , and `PayoutCheckerTask::run_once` immediately calls `handle_finalized_payout` for any such match [4](#0-3) .
- `handle_finalized_payout` proceeds to reserve a kickoff connector, sign, and queue Kickoff/Reimburse transactions for the operator without ever verifying that operator actually funded the on-chain payout tx [5](#0-4) .
- `is_kickoff_malicious` only checks that the DB-recorded OP_RETURN operator matches the operator that later sends a kickoff (self-consistency), and that the committed blockhash matches — it never checks who actually funded the payout tx [6](#0-5) .
- `validate_payer_is_operator` likewise only compares the DB-stored payer pubkey to `self.signer.xonly_public_key`, i.e. it trusts the same unverified field [7](#0-6) .

Because the withdrawer holds the `SinglePlusAnyoneCanPay`-signed input (as documented: "User's withdrawal input... with the signature given to operators off-chain") [8](#0-7) , an attacker who is the withdrawer can build and broadcast their own payout tx consuming that same input and stamp an arbitrary victim operator's xonly_pk in the OP_RETURN. No component in the observed chain (`update_finalized_payouts` → DB → `get_first_unhandled_payout_by_operator_xonly_pk` → `handle_finalized_payout` → `is_kickoff_malicious` / `validate_payer_is_operator`) cryptographically verifies that the named operator actually contributed the funds paid to the user in that specific transaction. The named operator is later credited and reimbursed through the normal kickoff/round/reimburse machinery, which only checks self-consistency (DB value vs. the operator who eventually kicks off), never true funding provenance.

### Impact Explanation
A victim (or arbitrary) operator's `PayoutCheckerTask` will autonomously treat an attacker-authored payout as its own front and drive it through `handle_finalized_payout`, consuming one of that operator's kickoff connectors/collateral cycle and ultimately claiming a legitimate on-chain reimbursement from the bridge's round/reimburse UTXOs for a withdrawal that operator never funded. This is a real value drain from the bridge's collective reimbursement pool that is not matched by a genuine fronting payment from the credited operator, fitting the Critical category "an operator reimbursed for a payout it never funded." The attack is repeatable per withdrawal the attacker controls (any withdrawer-owned `SinglePlusAnyoneCanPay` signature can be used this way), and can target any operator whose xonly_pk is public knowledge.

### Likelihood Explanation
The attacker only needs to be the withdrawer (unprivileged), obtain their own `SinglePlusAnyoneCanPay` signature (which they inherently hold as the signer), and construct/broadcast one Bitcoin transaction with a chosen OP_RETURN payload — all within the stated unprivileged attacker capabilities (choosing UTXO bytes, signature/sighash flag, OP_RETURN, and broadcasting). No special timing race or front-running beyond normal transaction construction is required, and cost is limited to standard transaction fees.

### Recommendation
Cryptographically bind the OP_RETURN operator attribution to actual funding provenance — e.g., require the payout tx to include a second input/output structurally signed by the named operator (so the operator's own key must sign the transaction), or otherwise verify on-chain that the credited operator actually contributed funds to the specific payout output, rather than trusting an unauthenticated OP_RETURN byte string parsed by `update_finalized_payouts`.

### Proof of Concept
```
cargo test -p clementine-core payout_attribution_forgery
```
1. Run a single deposit + withdrawal e2e setup (as in `core/src/test/deposit_and_withdraw_e2e.rs`).
2. As the withdrawer, obtain the `SinglePlusAnyoneCanPay` signature over `withdrawal_utxo` normally handed to operators off-chain.
3. Build a payout tx with `create_payout_txhandler` using this signature but pass a *different, victim* operator's `xonly_pk` (an operator that never constructed this tx), and broadcast it.
4. Mine the block, run the verifier's `update_finalized_payouts`/`get_payout_info_from_move_txid`, and assert:
   - `payout_payer_operator_xonly_pk` (DB) == victim operator's xonly_pk, even though victim operator never signed/funded this payout tx.
5. Trigger the victim operator's `PayoutCheckerTask`/`handle_finalized_payout` and assert it proceeds to queue Kickoff/Reimburse transactions, confirming reimbursement is granted to an operator that never funded the payout.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L391-402)
```rust
/// # Inputs
/// 1. UTXO: User's withdrawal input (committed in Citrea side, with the signature given to operators off-chain)
///
/// # Outputs
/// 1. User payout output
/// 2. OP_RETURN output (with operators x-only pubkey that fronts the peg-out)
///
/// # Arguments
/// * `input_utxo` - The input UTXO for the payout, committed in Citrea side, with the signature given to operators off-chain.
/// * `output_txout` - The output TxOut for the user payout.
/// * `operator_xonly_pk` - The operator's x-only public key that fronts the peg-out.
/// * `user_sig` - The user's signature for the payout, given to operators off-chain.
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

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2342)
```rust
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

**File:** core/src/task/payout_checker.rs (L41-79)
```rust
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

**File:** core/src/operator.rs (L839-915)
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

**File:** core/src/operator.rs (L1710-1719)
```rust
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
```
