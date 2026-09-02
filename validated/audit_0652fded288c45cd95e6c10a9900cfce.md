### Title
Duplicate withdrawal UTXO across two `withdrawals` rows lets one payout tx mark two withdrawals `is_payout_handled=true` - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` calls `get_payout_txs_for_withdrawal_utxos`, which joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)`. If two distinct withdrawal index rows (`idx=i`, `idx=j`) are populated with the identical `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, a single Bitcoin transaction spending that UTXO produces two result rows, both of which get `payout_txid`/`payout_payer_operator_xonly_pk` set by `update_payout_txs_and_payer_operator_xonly_pk`. `PayoutCheckerTask::run_once` then independently processes and marks both `idx=i` and `idx=j` as `is_payout_handled=true`, each producing its own kickoff/reimbursement claim, for a single real BTC outflow.

### Finding Description
The binding that should hold is: **number of `withdrawals` rows with `is_payout_handled=true` for a given spent withdrawal UTXO `U` == 1** (one on-chain payout spends `U` once, so exactly one reimbursement-eligible claim should exist for it).

Trace:
1. `withdrawal_id` in the Citrea Bridge contract is used interchangeably as the deposit index (`Verifier::sign_optimistic_payout` and `Operator::withdraw` both call `get_move_to_vault_txid_from_citrea_deposit(deposit_id)`/`get_withdrawal_utxo_from_citrea_withdrawal(withdrawal_index)` on the same `idx`), [1](#0-0) [2](#0-1) . Each `idx` is a distinct deposit slot.
2. `update_citrea_deposit_and_withdrawals` blindly writes whatever `(idx, OutPoint)` pairs `collect_withdrawal_utxos` reports from Citrea into `withdrawals.withdrawal_utxo_txid/vout` via `update_withdrawal_utxo_from_citrea_withdrawal`, with no check that the UTXO is not already assigned to a different `idx` [3](#0-2) .
3. The `withdrawals` table has no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` - only `idx` is the primary key [4](#0-3) .
4. `get_payout_txs_for_withdrawal_utxos` joins on `(txid, vout)` only, so if two `idx` rows share the same UTXO, one spend produces two rows [5](#0-4) .
5. `update_finalized_payouts` iterates every `(idx, payout_txid)` pair returned and calls `update_payout_txs_and_payer_operator_xonly_pk` for all of them, setting the same `payout_txid`/operator key/blockhash on both rows [6](#0-5) .
6. `PayoutCheckerTask::run_once` selects `get_first_unhandled_payout_by_operator_xonly_pk` (ordered by `idx`, `LIMIT 1`) and, being invoked repeatedly, will process both `idx=i` and `idx=j` in separate iterations, each calling `handle_finalized_payout` (which creates a kickoff keyed off the row's own `move_to_vault_txid`/deposit outpoint) and `mark_payout_handled`, independently [7](#0-6) [8](#0-7) [9](#0-8) .

No code path checks that a UTXO already credited to one `idx` cannot be credited to another; the `get_payout_txs_for_withdrawal_utxos` join and the `mark_payout_handled` UPDATE operate purely per-`idx` with no cross-row dedup, and no DB uniqueness constraint exists on the UTXO columns.

### Impact Explanation
Each `idx` row represents a distinct deposit's fronted withdrawal amount (`bridge_amount`). If the same withdrawal UTXO is registered against two deposit indices, one actual Bitcoin payout transaction spending that UTXO causes both deposits' `withdrawals` rows to become `is_payout_handled=true`, each spawning its own kickoff via `handle_finalized_payout`. Since `Reimburse` eligibility is driven by `is_payout_handled`/`kickoff_txid` per `idx`, this credits the operator with reimbursement for two deposits (`2x bridge_amount`) while it only actually funded one payout output on Bitcoin. This is "an operator reimbursed for a payout it never funded" - Critical severity per the audit's impact list. The effect is repeatable across any pair of withdrawal indices that end up sharing a UTXO and across operators, since the flaw is in shared verifier logic, not operator-specific code.

### Likelihood Explanation
The only precondition inside this repo's trust boundary is that Citrea reports the same withdrawal UTXO for two different withdrawal indices via `collect_withdrawal_utxos`/`update_citrea_deposit_and_withdrawals`. Whether the Citrea Bridge contract itself permits registering a duplicate UTXO across two `withdrawal_id`s is a Citrea-contract-level question outside this repo and cannot be confirmed from this codebase; this repo unconditionally trusts and persists whatever Citrea reports with no defensive de-duplication of its own. Given the prompt's stated precondition (attacker can choose the bytes of a withdrawal UTXO in `withdraw()` calls for any withdrawal_id slot), if the contract does not itself enforce global UTXO uniqueness, exploitation requires only two withdraw() registrations and normal deposit/withdrawal flow, with no verifier/operator/aggregator privilege needed.

### Recommendation
Add a per-UTXO idempotency guard in this repo independent of Citrea's contract behavior: enforce a uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table (excluding NULLs), and/or have `update_withdrawal_utxo_from_citrea_withdrawal` reject/flag an update if the UTXO is already assigned to a different `idx`. Additionally, `get_payout_txs_for_withdrawal_utxos` / `update_finalized_payouts` should only credit a single `idx` per distinct spent UTXO (e.g., pick the lowest `idx` or explicitly detect and refuse to process duplicates), and `PayoutCheckerTask` should treat a duplicate-UTXO condition as an error requiring manual/verifier intervention rather than silently marking multiple withdrawals handled.

### Proof of Concept
```
cargo test -p core update_get_payout_txs_from_citrea_withdrawal_duplicate_utxo -- --exact
```
Test plan (extend `core/src/database/verifier.rs` tests module):
1. Insert `idx=i` and `idx=j` (`i != j`) via `upsert_move_to_vault_txid_from_citrea_deposit` with two distinct `move_to_vault_txid`s.
2. Call `update_withdrawal_utxo_from_citrea_withdrawal` for both `idx=i` and `idx=j` with the identical `OutPoint U`.
3. Insert one `bitcoin_syncer_spent_utxos` row for `U` spent by a single `payout_txid` (`insert_spent_utxo`).
4. Call `get_payout_txs_for_withdrawal_utxos(block_id)` and assert `result.len() == 1` (binding LHS) - it will actually return `2` (both `idx=i` and `idx=j`), proving the binding is broken.
5. Call `update_payout_txs_and_payer_operator_xonly_pk` with both rows, then assert via `get_first_unhandled_payout_by_operator_xonly_pk` + `mark_payout_handled` loop that only one `idx` ends up with `is_payout_handled=true`; the test will show both `idx=i` and `idx=j` become `is_payout_handled=true`, confirming double reimbursement eligibility for one on-chain spend.

### Citations

**File:** core/src/verifier.rs (L1625-1650)
```rust
        // check if withdrawal is valid first
        let move_txid = self
            .db
            .get_move_to_vault_txid_from_citrea_deposit(None, deposit_id)
            .await?
            .ok_or_else(|| {
                BridgeError::from(eyre::eyre!("Deposit not found for id: {}", deposit_id))
            })?;

        // amount in move_tx is exactly the bridge amount
        if output_amount
            > self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
        {
            return Err(eyre::eyre!(
                "Output amount is greater than the bridge amount: {} > {}",
                output_amount,
                self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
            )
            .into());
        }

        // check if withdrawal utxo is correct
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
            .await?;
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

**File:** core/src/verifier.rs (L2288-2350)
```rust
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

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

**File:** core/src/operator.rs (L588-596)
```rust
        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
```

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

**File:** core/src/database/verifier.rs (L170-196)
```rust
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

**File:** core/src/task/payout_checker.rs (L39-106)
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
```
