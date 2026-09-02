The claimed binding is real and the exploit path is confirmed by the code.

**Binding claimed:** `withdrawals.payout_payer_operator_xonly_pk` for withdrawal idx *i* == the operator that funded (owns output 0 of) the transaction spending `withdrawal_utxo` idx *i*. This binding must hold so `PayoutCheckerTask`/`Operator::validate_payer_is_operator` can later match the correct operator for reimbursement, and so `Verifier::is_kickoff_malicious` doesn't wrongly flag an honest operator's kickoff.

Tracing confirms the break:

- `create_payout_txhandler` builds outputs `[user_payout, anchor, OP_RETURN(operator_xonly_pk)]` and only signs input 0 with the user's signature. [1](#0-0) 
- `parse_withdrawal_sig_params` mandates (and even coerces) the sighash flag to `SinglePlusAnyoneCanPay`. [2](#0-1) 
- `Operator::withdraw` verifies the sig with `calculate_sighash_txin(0, in_signature.sighash_type)`, and `calculate_pubkey_spend_sighash` for `SinglePlusAnyoneCanPay` uses `Prevouts::One(0, ...)`, i.e. commits only to input 0 and (via SIGHASH_SINGLE) output 0 — outputs 1 (anchor) and 2 (OP_RETURN) and any other inputs are unconstrained. [3](#0-2) [4](#0-3) 
- On-chain matching is purely by *which tx spends the withdrawal outpoint*, independent of who built it: `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` on `(txid, vout)` of the withdrawal UTXO and returns whatever `spending_txid` actually confirmed. [5](#0-4) 
- `update_finalized_payouts` then extracts the operator pubkey solely from that confirmed tx's OP_RETURN output; if it's missing/garbled, `operator_xonly_pk` is `None` and gets persisted as `NULL`. [6](#0-5) [7](#0-6) 

Because the signature only commits to input 0 + output 0, any party can take the broadcast honest tx, keep input 0 (same witness, since it's a key-path spend needing only the signature) and output 0 byte-for-byte, and replace the anchor output and OP_RETURN output (or add/remove other inputs, since ANYONECANPAY doesn't commit non-signed inputs) — the signature stays valid. If this variant confirms instead of the original, the DB binds `payout_payer_operator_xonly_pk = NULL` for that withdrawal, even though the honest operator actually funded output 0.

Downstream damage is worse than just "unreimbursable": `get_first_unhandled_payout_by_operator_xonly_pk` filters `WHERE payout_payer_operator_xonly_pk = $1`, so a `NULL` row is never returned to any operator — the honest operator can never be matched and reimbursed for BTC it fronted. [8](#0-7) 
Separately, `Verifier::is_kickoff_malicious` treats a missing/mismatched OP_RETURN operator pubkey as proof the kickoff is malicious, which would cause verifiers to challenge/disprove the honest operator's kickoff — burning its collateral. [9](#0-8) 

None of the checked guards prevent this: `Verifier::is_deposit_valid`, `SECP.verify_schnorr`, and the sighash-type enforcement in `parse_withdrawal_sig_params` all only ensure the *signature* is valid for input 0/output 0 — none of them constrain or authenticate the OP_RETURN output or any other outputs/inputs of the confirmed transaction.

### Title
Payout OP_RETURN operator-attribution can be stripped via SIGHASH_SINGLE|ANYONECANPAY malleability, permanently orphaning the fronting operator's reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler`'s user signature only commits to input 0 and output 0 (`SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params`), leaving the anchor output and the operator-identifying OP_RETURN output (index 2) completely unauthenticated. Anyone observing the broadcast/mempool payout tx can reuse input 0's witness verbatim in a new transaction with a mangled or missing OP_RETURN, and if that variant confirms, `Verifier::update_finalized_payouts` records `payout_payer_operator_xonly_pk = NULL` even though the honest operator actually funded the withdrawal (output 0).

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk[i] == operator that funded output 0 of the tx spending withdrawal_utxo[i]`.

`create_payout_txhandler` places `operator_xonly_pk.serialize()` in the OP_RETURN at output index 2, but only signs input 0 (key-path spend), and the sighash type accepted for this signature is fixed to `TapSighashType::SinglePlusAnyoneCanPay` in `parse_withdrawal_sig_params` (core/src/rpc/parser/operator.rs:170-187). `calculate_pubkey_spend_sighash` shows that for this sighash flag only input 0's prevout and (by SIGHASH_SINGLE) output 0 are committed to the signature hash — outputs 1 (anchor) and 2 (OP_RETURN), and any additional inputs, are entirely unauthenticated (core/src/builder/transaction/txhandler.rs:210-233). `Operator::withdraw` verifies the user signature exactly this way via `calculate_sighash_txin(0, in_signature.sighash_type)` (core/src/operator.rs:628-637).

Later, `Verifier::update_finalized_payouts` determines the payout's `operator_xonly_pk` purely from whichever transaction actually spent the `withdrawal_utxo` on-chain (matched by outpoint via `get_payout_txs_for_withdrawal_utxos`, core/src/database/verifier.rs:168-196), by parsing that confirmed tx's OP_RETURN output (core/src/verifier.rs:2311-2343). There is no check that this confirmed tx is the one the operator itself broadcast — only that it spends the same outpoint and has the same output 0.

Exploit: an attacker observes the honest operator's broadcast payout tx (input 0 = withdrawal UTXO, output 0 = user payout, valid `SinglePlusAnyoneCanPay` witness on input 0). The attacker crafts a new transaction reusing input 0's outpoint and exact witness, keeping output 0 byte-identical, but replacing output 2 (OP_RETURN) with garbage/empty data (and/or altering output 1 / adding other inputs, since these aren't committed). The signature remains valid because the sighash doesn't cover them. If the attacker's variant is the one that confirms (e.g., by paying a higher fee or reaching a miner first), the DB permanently records `payout_payer_operator_xonly_pk = NULL` for that withdrawal index.

### Impact Explanation
- `get_first_unhandled_payout_by_operator_xonly_pk` filters `WHERE payout_payer_operator_xonly_pk = $1`; a `NULL` row can never match any operator, so the honest operator that fronted the withdrawal BTC can never be selected for reimbursement via `PayoutCheckerTask` (core/src/database/verifier.rs:282-313, core/src/task/payout_checker.rs:31-113) — "an honest operator permanently unable to be reimbursed" (Critical).
- Separately, `Verifier::is_kickoff_malicious` treats a missing/mismatched OP_RETURN pubkey as proof of a malicious kickoff (core/src/verifier.rs:1871-1890), which can trigger challenge/disprove of the honest operator's legitimate kickoff, risking collateral burn — "an honest operator's collateral burned" (Critical).
- Repeatable per withdrawal/operator: any observed in-flight payout tx is vulnerable, with no dependency on which operator or deposit is involved.

### Likelihood Explanation
No privileged role is required — only the ability to observe a broadcast Bitcoin transaction (mempool) and to broadcast a competing transaction with a higher fee or otherwise get it mined preferentially. Attacker cost is limited to fees for the replacement transaction; the withdrawal amount itself is unaffected in this variant (the attacker doesn't gain the funds, they only strip attribution), making it a pure griefing/denial vector against a specific operator, repeatable across every withdrawal that operator services.

### Recommendation
Change the payout transaction so the operator-identifying OP_RETURN (and ideally all outputs) is committed by the signature that authorizes spending the withdrawal UTXO — e.g. require the user's signature to use `SIGHASH_ALL` (or `AllPlusAnyoneCanPay` at minimum) so output 2 is covered, or otherwise bind operator attribution to data that is signed/committed rather than to an arbitrary unauthenticated OP_RETURN in the confirmed spending transaction.

### Proof of Concept
1. In a `core/src/test` regtest environment, run the standard deposit + `Operator::withdraw` flow so operator A broadcasts a valid payout tx `T_honest` (input 0 = withdrawal UTXO, output 0 = user payout, output 2 = OP_RETURN with A's xonly pk).
2. Before `T_honest` confirms, construct `T_attacker`: same input 0 (same witness bytes), same output 0, but with output 2 replaced by an empty/garbled script (and optionally drop/replace the anchor output / add attacker-funded fee inputs).
3. Mine `T_attacker` instead of `T_honest` (regtest gives full control over which tx is included).
4. Run the verifier's Citrea sync so `update_finalized_payouts` processes the block containing `T_attacker`.
5. Assert `db.get_payout_info_from_move_txid(...)` returns `operator_xonly_pk == None` for that withdrawal, and assert `db.get_first_unhandled_payout_by_operator_xonly_pk(operator_A_pk)` returns `None`, proving operator A can never be matched for reimbursement despite having funded output 0.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L414-435)
```rust
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

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/builder/transaction/txhandler.rs (L210-233)
```rust
    pub fn calculate_pubkey_spend_sighash(
        &self,
        txin_index: usize,
        sighash_type: TapSighashType,
    ) -> Result<TapSighash, BridgeError> {
        let prevouts_vec: Vec<&TxOut> = self
            .txins
            .iter()
            .map(|s| s.get_spendable().get_prevout())
            .collect();
        let mut sighash_cache: SighashCache<&bitcoin::Transaction> =
            SighashCache::new(&self.cached_tx);
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
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

**File:** core/src/verifier.rs (L2311-2343)
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
        }
```
