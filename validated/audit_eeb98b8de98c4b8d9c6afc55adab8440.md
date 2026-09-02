## Finding Analysis

**Binding claimed broken:** `payout_payer_operator_xonly_pk` stored for withdrawal index `i` (used by `PayoutCheckerTask` and `is_kickoff_malicious` to decide who gets reimbursed) should equal the xonly_pk of the operator that actually broadcast/funded the payout satisfying withdrawal `i`. i.e. `attributed_operator(i) == actual_payer(i)`.

Tracing the code confirms this binding is **not enforced anywhere**, and the root cause is broader than an index-ordering mismatch between `create_payout_txhandler` and `get_first_op_return_output` — it is that the payout OP_RETURN (whichever output holds it) is **never covered by any signature at all**.

- `create_payout_txhandler` builds outputs in fixed order: payout(0), anchor(1), OP_RETURN(2), and the OP_RETURN contains only `operator_xonly_pk.serialize()` — 32 raw bytes, uncommitted to anything else. [1](#0-0) 
- The withdrawal signature is required to be `TapSighashType::SinglePlusAnyoneCanPay`, which per BIP341 only commits to input 0 and output 0. [2](#0-1) [3](#0-2) 
- `get_first_op_return_output` scans transaction outputs by `is_op_return()`, independent of position. [4](#0-3) 
- `update_finalized_payouts` uses this scan on the *mined* payout tx and accepts whatever 32 bytes are found as `operator_xonly_pk`, with only a syntactic (parseable-as-xonly-pk) check — no proof that this key belongs to whoever actually funded/broadcast the tx. [5](#0-4) 
- That value is persisted verbatim and later read back by `get_payout_info_from_move_txid` / `get_first_unhandled_payout_by_operator_xonly_pk`. [6](#0-5) 
- `PayoutCheckerTask::run_once` picks up "the first unhandled payout" **matching the operator's own key** and unconditionally proceeds to kickoff/reimbursement. [7](#0-6) 
- The only verifier-side guard, `is_kickoff_malicious`, checks internal consistency only: that the DB-recorded `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` of whoever sent the kickoff — it never checks that this operator actually funded the payout output. [8](#0-7) 

Because outputs 1 and 2 are unsigned, anyone in possession of the withdrawer's `SinglePlusAnyoneCanPay` signature (or, in the self-attack framing allowed by the ruleset, the withdrawing party itself, who chooses the signature/sighash flag and the OP_RETURN bytes) can construct and broadcast an alternate payout transaction that satisfies input0/output0 exactly, but substitutes an arbitrary 32-byte OP_RETURN — including a real, unrelated operator C's public xonly_pk copied from any other public payout transaction. `update_finalized_payouts` will then attribute withdrawal `i` to operator C. If C is a real running operator, C's own `PayoutCheckerTask` will pick this up as "its own" unhandled payout and drive the kickoff/reimburse flow to completion, since `is_kickoff_malicious` only checks self-consistency and passes.

### Title
Payout OP_RETURN operator attribution is unauthenticated, letting reimbursement be credited to an operator who never funded the payout - (File: core/src/verifier.rs)

### Summary
The payout transaction's OP_RETURN, which encodes the operator credited with fronting a withdrawal, is excluded from the `SinglePlusAnyoneCanPay` sighash and is accepted verbatim by `update_finalized_payouts` with only a syntactic xonly_pk check. Anyone controlling the mined payout transaction's non-output-0 contents can attribute the withdrawal to an arbitrary, unrelated operator's public key, and no downstream check (`is_kickoff_malicious`, `verify_storage_proofs`) verifies that the attributed operator actually paid the user.

### Finding Description
The binding "operator credited for withdrawal `i` == operator who funded withdrawal `i`" is broken. `create_payout_txhandler` places the OP_RETURN, containing only `operator_xonly_pk.serialize()`, as the transaction's third output [9](#0-8) , and the withdrawal signature is enforced to be `SinglePlusAnyoneCanPay` [10](#0-9) , which per Taproot sighash rules commits only to input 0 and output 0. Outputs 1 (anchor) and 2 (OP_RETURN) are therefore never authenticated by any signature.

`update_finalized_payouts` reconstructs `operator_xonly_pk` purely by scanning the confirmed payout transaction for the first OP_RETURN output and parsing its bytes as an `XOnlyPublicKey`, with no check that this data was produced by, or economically tied to, the entity that actually satisfied output 0 [11](#0-10) . The result is written straight into `withdrawals.payout_payer_operator_xonly_pk` [12](#0-11) .

Any party able to broadcast a Bitcoin transaction that reuses a valid `SinglePlusAnyoneCanPay` signature for input 0/output 0 of a withdrawal can freely choose the bytes of outputs 1 and 2, including copying a real, unrelated operator C's serialized xonly_pk from any previously mined payout transaction (public information). Once such a transaction confirms, `update_finalized_payouts` attributes withdrawal `i` to operator C. Operator C's own automation, `PayoutCheckerTask::run_once`, queries for "the first unhandled payout" matching its own key [13](#0-12)  and, finding this forged entry, drives `handle_finalized_payout` and the kickoff flow to completion. The verifier-side guard `is_kickoff_malicious` only checks that the DB-recorded operator equals the kickoff sender's operator xonly_pk [8](#0-7)  — a trivially true self-consistency check — never verifying that operator C actually spent funds to satisfy the withdrawal. No other guard (`verify_storage_proofs`, `SPV::verify`, `Verifier::is_deposit_valid`) checks economic linkage between the OP_RETURN attribution and the real payer, since these only validate that the withdrawal outpoint/output0 match Citrea's storage proof, not who controls outputs 1/2.

### Impact Explanation
This allows an operator to be reimbursed (`Reimburse` transaction spending the move-to-vault UTXO) for a withdrawal it never funded, matching the Critical category "an operator reimbursed for a payout it never funded." The blast radius is per-withdrawal and repeatable for every withdrawal processed on the bridge; any public payout OP_RETURN can be recycled against any future or past unconfirmed withdrawal that the attacker can influence the non-output-0 bytes of. It also degrades the honest operator whose withdrawal is falsely attributed elsewhere, since the true payer of output 0 receives no reimbursement credit for its economic outlay if it is not the one recorded in `payout_payer_operator_xonly_pk`.

### Likelihood Explanation
No mainnet/live-Citrea access is required to demonstrate the flaw; the check is a pure database/unit-level logic error reachable from `update_finalized_payouts` given any two real payout transactions. Practical exploitation on-chain requires the attacker (as the withdrawer, per the allowed capability set: they choose the withdrawal UTXO bytes, the Schnorr signature, its sighash flag, and the OP_RETURN) to construct and broadcast a payout transaction whose output 0 matches what the sighash pins, and whose OP_RETURN is forged — a low-cost, deterministic, and repeatable Bitcoin transaction construction with no dependency on validator collusion or key compromise.

### Recommendation
Commit the operator attribution to the signed portion of the transaction (e.g. require the operator xonly_pk to be part of output 0's script or amount, or require it as a signed covenant/commitment rather than an unsigned OP_RETURN), or otherwise cryptographically bind the OP_RETURN value to a signature/commitment produced only by the attributed operator (e.g., have the operator sign the payout OP_RETURN bytes and verify that signature in `update_finalized_payouts` before attribution).

### Proof of Concept
```
cargo test -p clementine-core update_finalized_payouts_rejects_unauthenticated_op_return
```
Test outline:
1. Create two deposits/withdrawals with move_txids `move_i` and `move_j`, each fronted for real by distinct operators `op_i` and `op_j` via `create_payout_txhandler`, producing valid mined payout transactions `payout_tx_i` (OP_RETURN = `op_i` pubkey) and `payout_tx_j` (OP_RETURN = `op_j` pubkey).
2. Construct a variant of `payout_tx_i` that keeps input 0 and output 0 identical (so the existing `SinglePlusAnyoneCanPay` signature remains valid) but replaces its OP_RETURN output with the bytes copied from `payout_tx_j`'s OP_RETURN (i.e., `op_j`'s pubkey).
3. Mine the variant transaction instead of the original `payout_tx_i`, and run `update_finalized_payouts`.
4. Assert `db.get_payout_info_from_move_txid(move_i).0 == Some(op_j_xonly_pk)`, demonstrating `attributed_operator(i) != actual_payer(i)` (`op_i`), i.e. the binding is violated with only unsigned-output tampering and no signature forgery.

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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
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

**File:** core/src/verifier.rs (L2312-2342)
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

**File:** core/src/database/verifier.rs (L253-313)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
                ))
            })
            .transpose()
    }

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
