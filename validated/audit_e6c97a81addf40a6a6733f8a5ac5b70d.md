### Title
Two Citrea withdrawal indices sharing an attacker-chosen `withdrawal_utxo` let `PayoutCheckerTask` mint two kickoffs for one fronted payout - (File: core/src/task/payout_checker.rs)

### Summary
`withdrawals.withdrawal_utxo_txid`/`withdrawal_utxo_vout` are written per-`idx` by `update_withdrawal_utxo_from_citrea_withdrawal` with no uniqueness constraint across `idx` values, and the withdrawal UTXO itself is attacker-chosen data submitted with the Citrea `withdraw()` call. If two different withdrawal indices are recorded with the identical `(txid, vout)`, `get_payout_txs_for_withdrawal_utxos` and `update_finalized_payouts` will populate both rows with the same `payout_txid` and the same `payout_payer_operator_xonly_pk`, and `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs`) will process each `idx` independently, invoking `Operator::handle_finalized_payout` and `mark_payout_handled` twice for what is physically one fronted Bitcoin payment.

### Finding Description
Binding claimed: `number_of_mark_payout_handled_calls_per_physical_payout == 1`. Trace shows this is not enforced anywhere in the queried code.

- `withdrawals` schema has no `UNIQUE` on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`: [1](#0-0) .
- `update_withdrawal_utxo_from_citrea_withdrawal` sets these columns per `idx` unconditionally, with no cross-`idx` dedup check: [2](#0-1) .
- `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`; two `idx` rows sharing the tuple both resolve to the same `spending_txid`: [3](#0-2) .
- `update_finalized_payouts` writes `payout_txid`, `payout_payer_operator_xonly_pk`, `payout_tx_blockhash` identically for every `idx` returned by the join above: [4](#0-3) .
- `get_first_unhandled_payout_by_operator_xonly_pk` selects strictly by `idx` (`ORDER BY w.idx ASC LIMIT 1`) with no exclusion of `idx`s whose `payout_txid`/`move_to_vault_txid` was already credited under a different `idx`: [5](#0-4) .
- `mark_payout_handled` only flips `is_payout_handled` for the single passed `idx`, it never checks or locks on `payout_txid`: [6](#0-5) .
- `PayoutCheckerTask::run_once` pulls one unhandled payout by operator key, resolves `deposit_data` from `move_to_vault_txid` (which differs per `idx`/deposit), calls `handle_finalized_payout`, then `mark_payout_handled` for that `idx` only: [7](#0-6) . On the next poll (every `PAYOUT_CHECKER_POLL_DELAY`), the second colliding `idx` is still unhandled and is processed identically, producing a second, independent `kickoff_txid`.

Root cause: identity/uniqueness of the "physically fronted payout" is tracked at the `withdrawals.idx` (deposit) granularity, but the actual real-world evidence used to authorize reimbursement (`payout_tx_blockhash` + the underlying spent Bitcoin UTXO) is not deduplicated across `idx` before minting a kickoff. Since the withdrawal UTXO bytes are attacker-supplied at `withdraw()` time (per the threat model given), an attacker can request two withdrawals (two different deposits/`idx`) with the identical destination UTXO bytes, causing one real Bitcoin spend to satisfy both DB rows.

No verification step in the traced path (`is_kickoff_malicious`, `validate_payer_is_operator`, `get_payout_info_from_move_txid`) cross-checks `payout_txid` uniqueness across `idx`; each of those checks operates per-deposit/per-move_txid and would pass independently for idx1 and idx2 since each has its own valid `deposit_data`/`move_to_vault_txid`.

### Impact Explanation
An honest operator who fronted exactly one withdrawal payout would, under this collision, be credited two `kickoff_txid`s (`is_payout_handled = TRUE`, `kickoff_txid` set) for two different deposits/vault UTXOs, using the same underlying Bitcoin payment as "proof" both times. This is a reimbursement-integrity break: the operator (or, since the attacker controls the withdrawal_utxo bytes used to create the collision, potentially a colluding/observing party) obtains kickoff-eligible reimbursement credit for a payout that was funded once — matching the Critical category "an operator reimbursed for a payout it never [fully] funded." Blast radius is per-operator and per-pair-of-deposits chosen by the attacker at withdrawal time, and is repeatable across any set of deposits the attacker controls withdrawals for.

### Likelihood Explanation
Preconditions: attacker must control two withdrawal requests (`withdraw()` calls on the Citrea Bridge contract) whose withdrawal UTXO bytes are made identical, and get an operator to front (or itself trigger fronting logic for) the payout. The rules explicitly grant the attacker the ability to "choose the bytes of a withdrawal UTXO," and no code path found enforces uniqueness of `(withdrawal_utxo_txid, withdrawal_utxo_vout)` across `idx`. Cost is limited to the fees for two Citrea `withdraw()` calls plus deposit setup; no verifier/aggregator privilege, key share, or hashrate is needed. Feasibility depends on operator behavior fronting both requests, which is standard operator duty once a matching payout appears eligible.

### Recommendation
Enforce uniqueness of the fronted-payout evidence, not just per-`idx` handling: add a DB uniqueness constraint (or explicit check in `mark_payout_handled`/`run_once`) that a given `payout_txid` (or `(withdrawal_utxo_txid, withdrawal_utxo_vout)` pair) can be consumed by at most one `withdrawals.idx`/kickoff. Reject or flag as malicious any second withdrawal index that maps to a `withdrawal_utxo` already bound to another `idx`'s payout, ideally at `update_withdrawal_utxo_from_citrea_withdrawal`/`update_finalized_payouts` time rather than at `PayoutCheckerTask` time.

### Proof of Concept
```rust
// core/src/task/payout_checker.rs collision test (conceptual, place under a #[cfg(test)] module outside excluded test files if reproducing manually)
#[tokio::test]
async fn payout_checker_double_credits_on_shared_withdrawal_utxo() {
    // 1. Seed two withdrawal rows idx1, idx2 with distinct move_to_vault_txid (distinct deposits)
    //    but IDENTICAL withdrawal_utxo_txid/vout via update_withdrawal_utxo_from_citrea_withdrawal.
    // 2. Insert one spent_utxo row for that shared (txid, vout) spent by payout_txid P in block B.
    // 3. Call get_payout_txs_for_withdrawal_utxos(block_id) -> expect [(idx1, P), (idx2, P)].
    // 4. Call update_finalized_payouts equivalent (update_payout_txs_and_payer_operator_xonly_pk)
    //    -> assert both idx1 and idx2 now have same payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash.
    // 5. Run PayoutCheckerTask::run_once() once -> assert idx1 is_payout_handled = TRUE, kickoff_txid1 set.
    // 6. Run PayoutCheckerTask::run_once() again -> BROKEN BEHAVIOR: idx2 is picked up,
    //    handle_finalized_payout succeeds again, mark_payout_handled(idx2, kickoff_txid2) is called.
    // Binding assertion (should hold, currently fails):
    //    count of kickoff_txids minted for payout_txid P == 1
    // Observed: 2 distinct kickoff_txid values (kickoff_txid1 != kickoff_txid2) both tied to the same payout_txid P.
    assert_ne!(kickoff_txid1, kickoff_txid2); // demonstrates the double-credit
}
```

### Citations

**File:** core/src/database/schema.sql (L269-281)
```sql
create table if not exists withdrawals (
    idx int primary key,
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

**File:** core/src/verifier.rs (L2298-2350)
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
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L39-111)
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

        dbtx.commit().await?;

        Ok(true)
    }
```
