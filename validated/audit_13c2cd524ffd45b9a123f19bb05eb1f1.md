### Title
Unauthenticated OP_RETURN payer attribution lets anyone falsely credit/deny operator payout credit for a withdrawal — ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` attributes a withdrawal's payout to whichever operator x-only pubkey appears in the OP_RETURN of the transaction that spends the Citrea-recorded `withdrawal_utxo` — with no check that the named operator actually signed or funded that transaction. Because the only cryptographic commitment the user provides is a `SinglePlusAnyoneCanPay` signature that binds only the withdrawal input and the single user-payout output (index 0), anyone who can obtain that signature can build a completely different, self-funded transaction that reuses it, adds their own funding inputs, and writes an arbitrary operator's x-only pubkey into the OP_RETURN.

### Finding Description
The broken binding: **operator xonly pk recorded for withdrawal `idx`** (`withdrawals.payout_payer_operator_xonly_pk`) **== the party whose funds actually paid the user in the mined payout output.**

`Operator::withdraw` builds the payout tx via `create_payout_txhandler`, whose only input is the withdrawal UTXO and whose signature is checked against a Taproot `SinglePlusAnyoneCanPay` sighash: [1](#0-0) 

`SinglePlusAnyoneCanPay` (`TapSighashType::SinglePlusAnyoneCanPay`) commits only to the input being spent and the single output at the same index (output 0, the user's payout output); it does not commit to any other input or to the anchor/OP_RETURN outputs, as shown by the sighash construction using `Prevouts::One`: [2](#0-1) 

This means the signature is reusable by anyone in a brand-new transaction as long as: (a) the withdrawal UTXO is spent at input index 0, and (b) output 0 exactly matches the user-committed script/amount. All other inputs (funding) and outputs (OP_RETURN, anchor) are entirely unconstrained by the signature.

`Verifier::update_finalized_payouts` then reads whichever transaction is recorded as having spent `withdrawal_utxo` and extracts the operator identity purely from the OP_RETURN bytes, with no signature check tying that identity to the actual signer/funder of the transaction: [3](#0-2) 

This is persisted unconditionally into the `withdrawals` table: [4](#0-3) 

Each operator's automated `PayoutCheckerTask` polls for withdrawals attributed to its own key and, without any further verification that it actually broadcast/funded that specific payout, immediately drives the reimbursement flow (`handle_finalized_payout`, kickoff, LCP fetch, round end): [5](#0-4) [6](#0-5) 

`Operator::handle_finalized_payout` consumes one of the operator's unused signed kickoff connectors and proceeds through the presigned kickoff/round/reimburse graph without checking whether the operator's own node actually created the payout transaction: [7](#0-6) 

Finally, the ZK bridge circuit that is checked during challenge/disprove also extracts the operator pubkey solely from the OP_RETURN bytes with no signature verification tying it to the actual funder: [8](#0-7) 

**Exploit flow:** The withdrawal's `withdrawal_utxo` outpoint, output script/amount, and the user's `SinglePlusAnyoneCanPay` signature are all attacker-visible (via Citrea's public withdraw call / a dropped mempool broadcast). The attacker builds a fresh transaction: input 0 = withdrawal UTXO + captured signature, output 0 = the exact committed user payout, plus attacker-funded inputs to cover the amount/fees, plus an OP_RETURN naming any operator's x-only pubkey (their own choice, not the actual payer). The attacker broadcasts and gets this mined before any legitimate operator's own `withdraw` call lands. `update_finalized_payouts` then permanently records the attacker-chosen attribution for this withdrawal index — since the UTXO is already spent, no operator can ever re-attribute it.

None of the listed guards (`Verifier::is_deposit_valid`, `SECP.verify_schnorr`, `verify_storage_proofs`, `SPV::verify`, `lc_proof_verifier`) check that the OP_RETURN-named operator is the one who supplied the funding inputs — they only check the withdrawal input/output that the user's signature already covers.

### Impact Explanation
Two Critical outcomes from the same root cause:
- **An operator reimbursed for a payout it never funded**: the named operator's automated `PayoutCheckerTask` will treat this as its own payout and drive the full kickoff/challenge-timeout/reimburse flow, claiming bridge collateral/round funds for a withdrawal it never fronted.
- **An honest operator permanently unable to be reimbursed**: if the attacker instead names an uninvolved or wrong operator, the honest operator who was racing to legitimately front the withdrawal finds the UTXO already spent and can never get correctly attributed/reimbursed for that withdrawal index — the DB row is immutable once set for that `idx`.

This is repeatable for any withdrawal index whose `withdrawal_utxo` and signature the attacker can observe before the legitimate operator's transaction confirms, and blast radius spans all deposits/withdrawals and all operators, since the flaw is structural (OP_RETURN attribution is unauthenticated everywhere it's consumed: verifier DB, operator polling task, and the bridge ZK circuit).

### Likelihood Explanation
Preconditions are realistic and require no privileged access: the withdrawal UTXO, output script/amount, and the user's off-chain `SinglePlusAnyoneCanPay` signature are exposed by the withdraw request flow (either via a public Citrea-side event/parameter or via an unconfirmed mempool broadcast, both of which are visible to any node/observer). Attacker cost is limited to funding the user's output amount plus network fees — no special timing beyond simple broadcast-and-mine race against the legitimate operator's own transaction. This is repeatable per withdrawal/operator.

### Recommendation
Bind the OP_RETURN operator identity cryptographically to the transaction's actual funder, e.g., require the operator to sign a commitment (e.g. via `SIGHASH_ALL`/`SIGHASH_DEFAULT` on a dedicated funding input, or a separate operator signature over the whole payout tx including the OP_RETURN) so that changing the OP_RETURN or funding source invalidates the transaction. Alternatively, require the operator's `withdraw` RPC to pre-register the exact payout tx (or its OP_RETURN commitment) in the verifier network before broadcast, and have `update_finalized_payouts` only accept attribution matching a pre-registered, operator-signed commitment, rejecting any mined tx whose OP_RETURN wasn't pre-committed by that operator.

### Proof of Concept
```
cargo test citrea_e2e --features automation -- update_finalized_payouts_false_attribution
```
Plan:
1. Use `generate_withdrawal_transaction_and_signature` to obtain `(dust_utxo, payout_txout, sig)` with `SinglePlusAnyoneCanPay`.
2. Do NOT call any operator's `withdraw` RPC. Instead, as a non-operator test client, build a new transaction: input0 = `dust_utxo` + `sig`, output0 = `payout_txout` (unchanged), plus a wallet-funded extra input/output, plus an OP_RETURN containing operator B's x-only pubkey (a different, uninvolved registered operator).
3. Broadcast and mine this transaction, then mine `DEFAULT_FINALITY_DEPTH` blocks.
4. Assert `Database::get_payout_info_from_move_txid` / `withdrawals.payout_payer_operator_xonly_pk` for this withdrawal idx equals operator B's pubkey, i.e. equality `payout_payer_operator_xonly_pk == operator_B_xonly_pk` holds even though operator B never called `withdraw` nor funded the output.
5. Assert operator B's `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk` returns this withdrawal, proving it will autonomously begin the kickoff/reimbursement flow for a payout it never funded.

### Citations

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

**File:** core/src/operator.rs (L839-860)
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
```

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
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

**File:** core/src/verifier.rs (L2298-2342)
```rust
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");
```
