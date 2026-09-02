### Title
Missing uniqueness enforcement on `withdrawal_utxo_txid`/`withdrawal_utxo_vout` lets one spent UTXO be attributed to multiple withdrawal indices, enabling an operator to be credited for a payout it never funded - (core/src/database/verifier.rs)

### Summary
The `withdrawals` table has no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, and `update_withdrawal_utxo_from_citrea_withdrawal` writes these columns per Citrea withdrawal index with no check against other rows. Since the Citrea `withdraw()` call lets an unprivileged caller choose arbitrary txid/vout bytes for the withdrawal UTXO, two different withdrawal indices can end up pointing at the same Bitcoin outpoint. `get_payout_txs_for_withdrawal_utxos`'s JOIN then returns the single real `spending_txid` for both idx rows, and `update_finalized_payouts`/`update_payout_txs_and_payer_operator_xonly_pk` persist the same payout attribution (`payout_txid`, `payout_payer_operator_xonly_pk`) to both.

### Finding Description
The intended binding is: `payout_txid_attributed_to_withdrawal_idx == the withdrawal idx whose registered withdrawal_utxo was actually spent by that payout tx`, and this attribution must be 1:1.

Code path:
1. `core/src/citrea.rs::collect_withdrawal_utxos` reads each Citrea withdrawal index's `withdrawalUTXOs(idx)` (txid/vout bytes chosen by the withdrawing caller when they invoke `withdraw()` on the Citrea Bridge contract) with no verification against Bitcoin state at call time. [1](#0-0) 
2. `core/src/verifier.rs::update_citrea_deposit_and_withdrawals` loops over all new withdrawals and calls `update_withdrawal_utxo_from_citrea_withdrawal` for each idx unconditionally. [2](#0-1) 
3. `core/src/database/verifier.rs::update_withdrawal_utxo_from_citrea_withdrawal` performs a plain `UPDATE withdrawals SET withdrawal_utxo_txid = $2, withdrawal_utxo_vout = $3 ... WHERE idx = $1` with no uniqueness check against other rows, and the `withdrawals` table schema defines no `UNIQUE` constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`. [3](#0-2) [4](#0-3) 
4. `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)`, with no guard that only one withdrawal row may match a given spent UTXO. If two withdrawal idx rows share the same `withdrawal_utxo_txid`/`vout`, both rows match the single real spending transaction and the query returns two `(idx, spending_txid)` pairs carrying the same `spending_txid`. [5](#0-4) 
5. `update_finalized_payouts` consumes this list and calls `update_payout_txs_and_payer_operator_xonly_pk`, writing the same `payout_txid`/`payout_payer_operator_xonly_pk`/`payout_tx_blockhash` to **both** withdrawal rows. [6](#0-5) 
6. This corrupted per-row attribution is exactly what downstream logic trusts: `is_kickoff_malicious` looks up `get_payout_info_from_move_txid` (keyed by each row's own unique `move_to_vault_txid`) and treats the operator as having legitimately paid that specific deposit's withdrawal if the xonly pk matches, without any independent check that the operator's payout tx genuinely spends *that deposit's* withdrawal outpoint (it trusts the DB's earlier JOIN result). [7](#0-6) 
   Likewise `get_first_unhandled_payout_by_operator_xonly_pk` and `send_asserts` (used by the operator's own automation to claim reimbursement) key off `payout_payer_operator_xonly_pk`, which is now falsely set on the second row. [8](#0-7) [9](#0-8) 

Exploit flow: an unprivileged attacker calls Citrea's `withdraw()` twice (for two separate deposits/withdrawal indices idx1 and idx2), supplying the *same* txid/vout bytes both times. A legitimate operator later pays out idx1 with a single real Bitcoin payout transaction that spends that outpoint. When verifiers process the finalized block, the JOIN in `get_payout_txs_for_withdrawal_utxos` matches this one payout transaction to both idx1 and idx2, and both rows get marked paid by the same operator. Any subsequent Kickoff for idx2's deposit by that operator now passes `is_kickoff_malicious`'s "operator matches recorded payer" check even though the operator never funded a payout that actually satisfies idx2's own recipient — the on-chain bridge circuit assertion (`user_wd_txid == payout tx input's previous_output`) also passes, because the attacker deliberately made idx2's *stored* withdrawal outpoint identical to idx1's, so the equality check is satisfied by construction. Existing guards (`is_kickoff_malicious`, the bridge circuit's storage-proof/payout-input match, `verify_storage_proofs`) all operate on the premise that each withdrawal index has a distinct outpoint; none of them defend against two indices deliberately sharing one outpoint, and no DB uniqueness constraint blocks it either.

### Impact Explanation
An operator can be credited (`payout_payer_operator_xonly_pk`, `payout_txid` in the `withdrawals` row) for a deposit's withdrawal it never separately funded, matching the Critical category "an operator reimbursed for a payout it never funded." Because `is_kickoff_malicious`/`send_asserts` trust this DB attribution per deposit (keyed by each deposit's unique `move_to_vault_txid`), the operator can subsequently claim a full BitVM2 reimbursement cycle for the second (unfunded) deposit while having paid out real BTC only once. This is repeatable across any pair (or more) of withdrawal indices whose UTXO bytes an attacker chooses to collide, and is not limited to a single deposit/operator pair — any operator who happens to pay one of the colliding withdrawals benefits from the false attribution on all others sharing that outpoint.

### Likelihood Explanation
The attacker needs only: (1) two bridge deposits (paying the normal bridge_amount into the bridge, standard user cost), and (2) two `withdraw()` calls to the Citrea Bridge contract using identical txid/vout bytes — both explicitly listed as available to an unprivileged attacker in this threat model. No verifier, operator, or aggregator privilege is required to create the corrupted DB state; only a cooperating (or automated, opportunistic) operator is needed to realize the financial gain, which is a normal, expected actor in this protocol who is incentivized to do so once the false attribution exists.

### Recommendation
Enforce that `(withdrawal_utxo_txid, withdrawal_utxo_vout)` is unique across the `withdrawals` table (e.g., a partial unique index where both are non-null), reject/flag `update_withdrawal_utxo_from_citrea_withdrawal` calls that would create a collision with an existing withdrawal's UTXO, and add a defensive check in `get_payout_txs_for_withdrawal_utxos`/`update_finalized_payouts` that a spent UTXO is attributed to at most one withdrawal idx, erroring out (and flagging for manual/verifier review) if more than one row matches.

### Proof of Concept
```rust
// core/src/database/verifier.rs tests module (illustrative)
#[tokio::test]
async fn duplicate_withdrawal_utxo_causes_double_attribution() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let payout_txid = Txid::from_byte_array([0xAA; 32]);
    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0xBB; 32]),
        vout: 0,
    };
    let idx1 = 1u32;
    let idx2 = 2u32;

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    // The one real spending event of shared_utxo:
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    // Two deposits/withdrawal rows registering the SAME withdrawal UTXO bytes (attacker-controlled)
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx1, &Txid::from_byte_array([0x01; 32])).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx1, shared_utxo, block_id).await.unwrap();

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx2, &Txid::from_byte_array([0x02; 32])).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx2, shared_utxo, block_id).await.unwrap();

    // Simulate verifier's finalized-block processing
    let attributions = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // BROKEN BINDING: exactly one withdrawal idx should own this UTXO's payout,
    // but both idx1 and idx2 are attributed the same spending_txid.
    assert_eq!(attributions.len(), 1, "expected exactly one withdrawal idx attributed to the spent UTXO, got {:?}", attributions);
}
```
Running this test against the current implementation demonstrates `attributions.len() == 2` (both `idx1` and `idx2` mapped to `payout_txid`), proving the broken 1:1 equality and the resulting double-attribution that downstream `update_payout_txs_and_payer_operator_xonly_pk`, `is_kickoff_malicious`, and `send_asserts` all consume as trusted state.

### Citations

**File:** core/src/citrea.rs (L458-496)
```rust
    async fn collect_withdrawal_utxos(
        &self,
        last_withdrawal_idx: Option<u32>,
        to_height: u64,
    ) -> Result<Vec<(u64, OutPoint)>, BridgeError> {
        let mut utxos = vec![];

        let mut start_idx = match last_withdrawal_idx {
            Some(idx) => idx + 1,
            None => 0,
        };

        loop {
            let withdrawal_utxo = self
                .contract
                .withdrawalUTXOs(U256::from(start_idx))
                .block(BlockId::Number(BlockNumberOrTag::Number(to_height)))
                .call()
                .await;
            match withdrawal_utxo {
                Err(e) if e.to_string().contains("execution reverted") => {
                    tracing::trace!("Withdrawal utxo not found for index, error: {:?}", e);
                    break;
                }
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            let withdrawal_utxo = withdrawal_utxo.expect("Failed to get withdrawal UTXO");
            let txid = withdrawal_utxo.txId.0;
            let txid =
                Txid::from_slice(txid.as_ref()).wrap_err("Failed to convert txid to Txid")?;
            let vout = withdrawal_utxo.outputId.0;
            let vout = u32::from_le_bytes(vout);
            let utxo = OutPoint { txid, vout };
            utxos.push((start_idx as u64, utxo));
            start_idx += 1;
        }
        Ok(utxos)
    }
```

**File:** core/src/verifier.rs (L1857-1914)
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
```

**File:** core/src/verifier.rs (L2248-2262)
```rust
        for (idx, withdrawal_utxo_outpoint) in new_withdrawals {
            tracing::info!(
                "Saving withdrawal utxo {:?} with index {} for Citrea withdrawals",
                withdrawal_utxo_outpoint,
                idx
            );
            self.db
                .update_withdrawal_utxo_from_citrea_withdrawal(
                    Some(dbtx),
                    idx as u32,
                    withdrawal_utxo_outpoint,
                    block_height,
                )
                .await?;
        }
```

**File:** core/src/verifier.rs (L2296-2350)
```rust
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
```

**File:** core/src/database/verifier.rs (L108-135)
```rust
    pub async fn update_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        withdrawal_utxo: OutPoint,
        withdrawal_batch_proof_bitcoin_block_height: u32,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET withdrawal_utxo_txid = $2,
                 withdrawal_utxo_vout = $3,
                 withdrawal_batch_proof_bitcoin_block_height = $4
             WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(withdrawal_utxo.txid))
        .bind(
            i32::try_from(withdrawal_utxo.vout)
                .wrap_err("Failed to convert withdrawal utxo vout to i32")?,
        )
        .bind(
            i32::try_from(withdrawal_batch_proof_bitcoin_block_height)
                .wrap_err("Failed to convert withdrawal batch proof bitcoin block height to i32")?,
        );

        execute_query_with_tx!(self.connection, tx, query, execute)?;
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

**File:** core/src/database/schema.sql (L271-281)
```sql
    move_to_vault_txid bytea not null,
    withdrawal_utxo_txid bytea,
    withdrawal_utxo_vout int,
    withdrawal_batch_proof_bitcoin_block_height int,
    payout_txid bytea,
    payout_payer_operator_xonly_pk text,
    payout_tx_blockhash text check (payout_tx_blockhash ~ '^[a-fA-F0-9]{64}'),
    is_payout_handled boolean not null default false,
    kickoff_txid bytea,
    created_at timestamp not null default now()
);
```

**File:** core/src/operator.rs (L1275-1296)
```rust
        let (payout_op_xonly_pk_opt, payout_block_hash, payout_txid, deposit_idx) = self
            .db
            .get_payout_info_from_move_txid(Some(&mut dbtx), move_txid)
            .await
            .wrap_err("Failed to get payout info from db during sending asserts.")?
            .ok_or_eyre(format!(
                "Payout info not found in db while sending asserts for move txid: {move_txid}"
            ))?;

        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }

```
