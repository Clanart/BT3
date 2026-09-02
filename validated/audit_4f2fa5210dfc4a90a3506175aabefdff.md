### Title
Payout OP_RETURN operator-attribution is not covered by the withdrawal signature, allowing reimbursement credit to be stolen - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs, core/src/task/payout_checker.rs)

### Summary
The user's withdrawal signature is required to use `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) [2](#0-1) , which under BIP-341 only commits to input 0 and output 0 — it does not cover output 2, the `OP_RETURN` output that names the reimbursing operator [3](#0-2) . Anyone who observes an honest operator X's broadcast/mempool payout tx can copy input 0 (same witness/signature, still valid) and output 0 (unchanged, required for SIGHASH_SINGLE validity), but freely rewrite the `OP_RETURN` to name a different real operator Y, then get that variant mined instead of X's original.

### Finding Description
The broken binding is: **the operator credited for withdrawal index `i`** (`withdrawals.payout_payer_operator_xonly_pk` as parsed from the mined payout tx's `OP_RETURN`) **== the operator whose funds actually paid output 0 of the mined payout tx for `i`**. This binding is broken because the field that encodes "who paid" (`OP_RETURN` operator xonly pubkey, output index 2) is outside the signed message of the only signature that authorizes spending the withdrawal UTXO.

Trace:
1. `create_payout_txhandler` builds outputs `[user_payout, anchor, OP_RETURN(operator_xonly_pk)]` and signs only input 0 with `set_p2tr_key_spend_witness` using the pre-supplied user signature [4](#0-3) .
2. `parse_withdrawal_sig_params` mandates `TapSighashType::SinglePlusAnyoneCanPay` for the user's signature [5](#0-4) , and `Operator::withdraw` verifies the signature against the sighash of only that spend [2](#0-1) . `SIGHASH_SINGLE` commits solely to the output at the same index as the input (index 0 here); `ANYONECANPAY` limits input commitment to just that one input. Outputs 1 (anchor) and 2 (`OP_RETURN`) are entirely uncommitted.
3. Because operator X broadcasts this payout tx to fund the withdrawal, the fully-formed transaction (including X's valid signature) is visible on the P2P network before/while unconfirmed. An attacker copies input 0's witness and output 0 verbatim into a new transaction, replaces output 2's `OP_RETURN` push-bytes with operator Y's x-only pubkey (any registered operator), adjusts fee/anchor as needed, and gets this variant mined (e.g. by outbidding X's version or via RBF/first-seen races), consuming the same withdrawal UTXO.
4. `update_finalized_payouts` scans the confirmed block, extracts the `OP_RETURN` pubkey from whichever tx actually spent the withdrawal UTXO (identified purely by outpoint match in `get_payout_txs_for_withdrawal_utxos`, with no check tying it to a specific broadcaster) [6](#0-5) [7](#0-6) , and stores `payout_payer_operator_xonly_pk = Y` [8](#0-7) .
5. `PayoutCheckerTask::run_once` for operator Y calls `get_first_unhandled_payout_by_operator_xonly_pk(Y)`, which matches on the stored (attacker-controlled) `payout_payer_operator_xonly_pk` column [9](#0-8) [10](#0-9) .
6. `Operator::handle_finalized_payout` (run as Y) allocates one of Y's own unused kickoff connectors and builds `kickoff_data.operator_xonly_pk = Y`'s own key [11](#0-10) , then `mark_payout_handled` permanently marks withdrawal `i` as handled [12](#0-11) .
7. Later, `Verifier::is_kickoff_malicious` re-checks `operator_xonly_pk` from `get_payout_info_from_move_txid` against `kickoff_data.operator_xonly_pk` [13](#0-12)  — but since the DB already stores Y (from the attacker-rewritten `OP_RETURN`), this check passes and does not catch the substitution. No verifier check ever confirms Y actually funded output 0; it only confirms self-consistency between the DB record and the kickoff.

X's original payout tx never confirms (input already spent by attacker's variant), so X can never trigger its own `handle_finalized_payout`/`mark_payout_handled` for index `i` — the row is already consumed by Y.

### Impact Explanation
Operator Y is reimbursed via the Reimburse tx / move-to-vault credit for a withdrawal it never funded (Y spent none of its own capital), while operator X — who genuinely fronted the user's withdrawal — permanently loses the ability to be reimbursed for that same withdrawal index because `is_payout_handled` is already `TRUE` and `payout_payer_operator_xonly_pk` is already fixed to Y. This is repeatable per withdrawal: any time an honest operator broadcasts a payout tx before it is deeply confirmed, any other registered operator's key can be substituted into `OP_RETURN` by a third party. This matches the Critical category: "an operator reimbursed for a payout it never funded" combined with "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Preconditions: at least two registered operators exist (X funds, Y is any other real operator, attacker doesn't need to control either), and the attacker only needs mempool/network visibility of X's broadcast payout tx plus the ability to broadcast a competing transaction with higher fee/faster propagation. No signature forgery is needed — the attacker reuses the existing valid `SinglePlusAnyoneCanPay` witness verbatim, since it doesn't cover the `OP_RETURN` output. This requires no operator, verifier, or aggregator privilege, fitting the stated unprivileged threat model (broadcast Bitcoin transactions, choose script/witness bytes). Cost is limited to bidding a competitive fee to win the race for confirmation.

### Recommendation
Bind the `OP_RETURN` operator attribution to the same signature that authorizes the withdrawal spend. Concretely: change the mandated sighash type to one that also commits to the `OP_RETURN` output (e.g. require `SIGHASH_ALL`/`SIGHASH_ALL|ANYONECANPAY` is not correct either since ANYONECANPAY still needs to cover outputs; use `SIGHASH_ALL` covering all outputs, or have the operator itself sign/commit to the `OP_RETURN` content as part of a covenant enforced elsewhere), or alternatively have `update_finalized_payouts`/`get_first_unhandled_payout_by_operator_xonly_pk` cross-check that the operator named in `OP_RETURN` corresponds to the operator that was actually authorized/expected for that withdrawal (e.g. compare against the operator that called `withdraw`/registered intent to front it, tracked independently of the mutable on-chain `OP_RETURN`).

### Proof of Concept
```
cargo test payout_op_return_hijack_credits_wrong_operator -- --nocapture
```
Test plan:
1. Set up two operator identities X and Y plus a `MockCitreaClientT` withdrawal registration for index `i` (as in existing e2e harness, e.g. `core/src/test/deposit_and_withdraw_e2e.rs`).
2. Have X call `Operator::withdraw` to build and sign its payout tx (`create_payout_txhandler`) with a valid `SinglePlusAnyoneCanPay` user signature.
3. Before mining, construct an attacker variant transaction: same input (outpoint + witness from X's tx), same output 0, but replace output 2's `OP_RETURN` push-bytes with Y's `serialize()`d x-only pubkey. Confirm the copied witness still passes `SECP.verify_schnorr` against the recomputed sighash (should succeed since sighash excludes output 2).
4. Mine the attacker's variant instead of X's original tx.
5. Run block sync (`update_finalized_payouts`) and assert:
   - `db.get_payout_info_from_move_txid(None, move_txid)` returns operator `Y`, not `X`.
   - `db.get_first_unhandled_payout_by_operator_xonly_pk(Some(&mut dbtx), Y)` returns `Some((i, move_txid, blockhash))`.
   - `db.get_first_unhandled_payout_by_operator_xonly_pk(Some(&mut dbtx), X)` returns `None`.
6. Run `PayoutCheckerTask::run_once` for Y's operator instance and assert it succeeds, calling `handle_finalized_payout` and `mark_payout_handled(i, kickoff_txid)` under Y.
7. Assert that subsequently running `PayoutCheckerTask::run_once` for X's operator instance returns `Ok(false)` (no unhandled payout), proving X can never be reimbursed for index `i`.

### Citations

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L839-892)
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

```

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-435)
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
```

**File:** core/src/verifier.rs (L1871-1890)
```rust
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

**File:** core/src/verifier.rs (L2311-2321)
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
```

**File:** core/src/verifier.rs (L2345-2350)
```rust
        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
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

**File:** core/src/database/verifier.rs (L348-362)
```rust
    pub async fn mark_payout_handled(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        kickoff_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals SET is_payout_handled = TRUE, kickoff_txid = $2 WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(kickoff_txid));

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/task/payout_checker.rs (L39-51)
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
```
