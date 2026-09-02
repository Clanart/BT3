### Title
Withdrawal-to-payout mapping is not unique per `idx` — two withdrawal indices sharing one Bitcoin UTXO let an operator be reimbursed twice for a single payout - ([File: core/src/database/verifier.rs], [File: core/src/database/schema.sql], [File: core/src/verifier.rs])

### Summary
The `withdrawals` table has no `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint, and `update_withdrawal_utxo_from_citrea_withdrawal` blindly `UPDATE`s the row for a given `idx` with attacker-controlled UTXO bytes, without checking whether another `idx` already owns that outpoint. Because `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` on `(txid, vout)` rather than on `idx`, two distinct withdrawal indices pointing at the same physical outpoint will both match the single spend of that UTXO, producing two `(idx, spending_txid)` rows for one real Bitcoin payment.

### Finding Description
The invariant that should hold is: `spend(withdrawal_utxo) == exactly one (idx, payout_txid) pair`. The `withdrawals` schema enforces uniqueness only on `idx` (primary key), not on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` [1](#0-0) .

`Verifier::update_citrea_deposit_and_withdrawals` iterates every new Citrea withdrawal `(idx, withdrawal_utxo_outpoint)` and calls `update_withdrawal_utxo_from_citrea_withdrawal` unconditionally for each `idx`, with no cross-idx check that the outpoint is not already assigned to a different `idx` [2](#0-1) . The underlying query is a plain `UPDATE ... WHERE idx = $1` that writes `withdrawal_utxo_txid`/`vout` for the given row regardless of what other rows already contain [3](#0-2) .

Later, `get_payout_txs_for_withdrawal_utxos` correlates a spent outpoint to a withdrawal purely by `(txid, vout)`: [4](#0-3) 
If two different `idx` rows (`N` and `M`) share the same `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, this `JOIN` returns two rows, `(N, spending_txid)` and `(M, spending_txid)`, for the single real Bitcoin spend. `update_payout_txs_and_payer_operator_xonly_pk` then writes the same `payout_txid`/`payout_payer_operator_xonly_pk`/`payout_tx_blockhash` into both `idx` rows [5](#0-4) . From that point, `get_first_unhandled_payout_by_operator_xonly_pk` will surface both `idx N` and `idx M` as separate unhandled payouts for the same operator, keyed only by `payout_payer_operator_xonly_pk` and `is_payout_handled = FALSE` [6](#0-5) , each of which is expected to drive an independent reimbursement/kickoff cycle for a distinct deposit.

The root cause is that no component in this path re-derives or checks "one UTXO can only fund one payout": the schema lacks the constraint, the population loop lacks a conflict check, and the correlation query lacks `idx`-scoped disambiguation.

### Impact Explanation
If the attacker (as granted by the capabilities in this exercise) can get Citrea to register two withdrawal indices `N != M` that resolve to an identical `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, a single real Bitcoin payment satisfying withdrawal `N` will also be recorded as satisfying withdrawal `M`. This lets the paying operator collect reimbursement/collateral release for deposit `M` without ever having funded a matching payout for it — "an operator reimbursed for a payout it never funded," which is explicitly listed as Critical impact. The bug is systemic in the mapping layer (not limited to one deposit/operator pair): every deposit/withdrawal `idx` and every operator using this shared table is exposed, and it can be repeated for every colliding `idx` pair the attacker can arrange.

### Likelihood Explanation
Exploitation is gated entirely on whether Citrea's Bridge contract/light-client allows a withdrawer to specify an already-used or colliding withdrawal UTXO for two separate `withdraw_id`s — a precondition asserted as attacker capability in this exercise but whose actual enforcement lives in the Citrea contract, which is out of this repo's scope to verify. Within `core/`, there is no independent defense-in-depth check (DB constraint, application-level uniqueness check, or `idx`-aware join) that would catch or prevent the collision even if the Citrea side allowed it, so if the precondition holds, the Clementine-side logic traced above fully permits duplicate crediting.

### Recommendation
- Add `UNIQUE (withdrawal_utxo_txid, withdrawal_utxo_vout)` to the `withdrawals` table (or a partial unique index where both columns are `NOT NULL`), and make `update_withdrawal_utxo_from_citrea_withdrawal` reject/alert on a conflict rather than silently overwriting.
- Change `get_payout_txs_for_withdrawal_utxos`'s join to also validate/deduplicate by `idx`, and add an application-level check in `update_citrea_deposit_and_withdrawals` that a withdrawal UTXO is not already bound to a different `idx` before accepting the new mapping.

### Proof of Concept
Using the existing DB test harness pattern in `core/src/database/verifier.rs::update_get_payout_txs_from_citrea_withdrawal`:
1. Insert two deposits `idx = 0x1F` and `idx = 0x2F` via `upsert_move_to_vault_txid_from_citrea_deposit` with distinct `move_to_vault_txid`s.
2. Call `update_withdrawal_utxo_from_citrea_withdrawal` for both `idx` values with the **same** `OutPoint` (`utxo`).
3. Insert a single spent-UTXO row for that `OutPoint` via `insert_spent_utxo` with one `spending_txid`.
4. Call `get_payout_txs_for_withdrawal_utxos(block_id)` and assert it returns **two** rows, `(0x1F, spending_txid)` and `(0x2F, spending_txid)`, for the one real Bitcoin spend.
5. Call `update_payout_txs_and_payer_operator_xonly_pk` with both tuples using the same operator key; assert `get_first_unhandled_payout_by_operator_xonly_pk` yields both `idx` as separate unhandled payouts for that operator, proving a single Bitcoin payment is recorded as funding two distinct withdrawal indices.

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
