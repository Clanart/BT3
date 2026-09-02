### Title
Payout attribution is unauthenticated OP_RETURN data, allowing anyone to hijack an operator's fronted payout by racing the withdrawal UTXO spend - (File: core/src/verifier.rs, circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`Verifier::update_finalized_payouts` attributes a payout to an operator solely from an `OP_RETURN` push in whichever transaction is found on-chain spending the `withdrawal_utxo`, matched only by outpoint via `get_payout_txs_for_withdrawal_utxos` (join on `txid`/`vout`, not on a specific expected txid). Because `create_payout_txhandler` signs input 0 with `SinglePlusAnyoneCanPay` (covering only input 0 + output 0), any third party can copy the witness verbatim into a new transaction with an arbitrary output 2 and a higher fee, get it mined first, and have the DB record `payout_payer_operator_xonly_pk` as `None` or an arbitrary key instead of the honest fronting operator's key.

### Finding Description
The broken binding: `withdrawals.payout_payer_operator_xonly_pk` for withdrawal idx `i` should equal the xonly-pk of the operator whose wallet actually funded `output[0]` (the withdrawer's payout) by spending the same `withdrawal_utxo` input.

Code path:
1. `Operator::withdraw` builds the payout tx via `create_payout_txhandler` [1](#0-0) , using `SpendPath::KeySpend` on input 0 and a separate `op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()))` as output 2.
2. The user's signature is verified only against the `SinglePlusAnyoneCanPay` sighash of input 0 [2](#0-1) ; that sighash type by definition does not commit to output 2 (or output 1), and `ANYONECANPAY` does not commit to any other inputs.
3. `bitcoin_syncer` records spends purely by outpoint (`bitcoin_syncer_spent_utxos`), independent of which specific txid was expected [3](#0-2) .
4. `get_payout_txs_for_withdrawal_utxos` looks up the payout txid purely by matching `withdrawal_utxo_txid`/`vout` against `bitcoin_syncer_spent_utxos`, with no cross-check to a specific expected/pre-signed txid [4](#0-3) .
5. `Verifier::update_finalized_payouts` then extracts `operator_xonly_pk` purely from `get_first_op_return_output` + `parse_op_return_data` of whichever tx was found in step 4 [5](#0-4) , with no signature or commitment check tying that OP_RETURN to the actual signer/funder of input 0.
6. `parse_op_return_data` merely reads the first push after `OP_RETURN` [6](#0-5) ; it performs no validation that this data is authenticated by anything.

Attacker flow: observe the honest operator's broadcast/mempool payout_tx (inputs/outputs 0 signed with `SinglePlusAnyoneCanPay`), copy input 0 (same outpoint + witness) and output 0 verbatim into a new transaction, replace output 2 with a bogus/absent/attacker OP_RETURN, add higher fee, broadcast, get mined first. `update_finalized_payouts` records `payout_payer_operator_xonly_pk = None` or an arbitrary/attacker-chosen key for withdrawal idx `i`.

Downstream, `Operator::validate_payer_is_operator` requires `payer_xonly_pk == self.signer.xonly_public_key` read from this same corrupted column [7](#0-6) , and `PayoutCheckerTask::run_once` only picks up withdrawals via `get_first_unhandled_payout_by_operator_xonly_pk` filtered by `payout_payer_operator_xonly_pk = $1` [8](#0-7) [9](#0-8) . With the column wrong, the honest fronting operator's `handle_finalized_payout`/kickoff/reimburse path for this withdrawal is never triggered.

None of the listed guards (`is_deposit_valid`, `SECP.verify_schnorr`, presigned tx graph, DB uniqueness) prevent this because the withdrawal UTXO is a plain user-controlled taproot key-spend UTXO signed with a sighash flag that intentionally leaves outputs 1 and 2 unauthenticated — this is a genuine transaction-malleability/attribution gap, not a signature forgery.

### Impact Explanation
The fronting operator loses attribution for a real payout it funded, permanently blocking its Reimburse path for that withdrawal (`validate_payer_is_operator` fails forever; `get_first_unhandled_payout_by_operator_xonly_pk` never returns this row for that operator xonly-pk). This matches "an honest operator permanently unable to be reimbursed" — Critical severity. The attack is repeatable per fronted withdrawal (any time an operator broadcasts a payout before it confirms), across any operator and deposit, without needing any privileged role, key, or collateral — the attacker only needs to observe the mempool and pay a higher fee.

### Likelihood Explanation
Preconditions are realistic and inexpensive: a normal deposit/withdrawal flow with an operator fronting payout via `Operator::withdraw`, mempool visibility of the pending payout tx, and the ability to submit a higher-feerate competing transaction reusing the same witness (standard RBF-style transaction relay, no protocol violation). Attacker cost is just fees, no BTC principal at risk, and it is repeatable on every fronted withdrawal.

### Recommendation
Do not derive payout attribution solely from unauthenticated OP_RETURN bytes matched to a UTXO spend. Options: commit the operator xonly-pk under the same signature that authorizes the spend (e.g., have the user's signature use `AllSighashType` or a sighash that also commits to the OP_RETURN output, or route attribution via the operator's own separately-authenticated kickoff/round-tx binding), or require `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` to match against a specific pre-recorded expected payout txid (e.g., the exact txid the operator's TxSender broadcast) instead of matching any transaction that spends the outpoint.

### Proof of Concept
`cargo test` (regtest, mock/local Citrea, no mainnet):
1. Run a real deposit + `withdraw` via operator 0, obtaining the signed `payout_tx` (via `create_payout_txhandler`) before it confirms.
2. Craft `attacker_tx`: copy `payout_tx.input[0]` (outpoint + witness) and `payout_tx.output[0]` verbatim; replace `output[2]`'s OP_RETURN with an unrelated/garbage 32 bytes; set a higher fee via a change/anchor output; broadcast and mine it first.
3. Assert `db.get_payout_txs_for_withdrawal_utxos(...)` / `update_finalized_payouts` records `payout_payer_operator_xonly_pk = None` (or wrong key) for this withdrawal idx.
4. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(operator_0_xonly_pk)` returns `None`, proving operator 0 — despite having funded output 0 — can never be attributed/reimbursed for this withdrawal.

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

**File:** core/src/operator.rs (L1687-1729)
```rust
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

**File:** core/src/bitcoin_syncer.rs (L143-164)
```rust
async fn save_transaction_spent_utxos(
    db: &Database,
    dbtx: DatabaseTransaction<'_>,
    tx: &bitcoin::Transaction,
    block_id: u32,
) -> Result<(), BridgeError> {
    let txid = tx.compute_txid();
    db.insert_txid_to_block(dbtx, block_id, &txid).await?;

    for input in &tx.input {
        db.insert_spent_utxo(
            dbtx,
            block_id,
            &txid,
            &input.previous_output.txid,
            input.previous_output.vout as i64,
        )
        .await?;
    }

    Ok(())
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

**File:** core/src/verifier.rs (L2311-2335)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L608-617)
```rust
/// Parses the OP_RETURN data from a Bitcoin script. It retrieves the first data push after an OP_RETURN.
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
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
