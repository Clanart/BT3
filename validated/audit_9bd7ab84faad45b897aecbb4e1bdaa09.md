### Title
Silent No-Op in `update_withdrawal_utxo_from_citrea_withdrawal` Permanently Locks Bridged BTC When Deposit Row Is Absent — (`File: core/src/database/verifier.rs`)

### Summary

`update_withdrawal_utxo_from_citrea_withdrawal` issues a bare `UPDATE … WHERE idx = $1` with no `RETURNING` clause and no rows-affected check. If the target row does not yet exist in the `withdrawals` table, the query silently updates zero rows and returns `Ok(())`. The caller (`update_citrea_deposit_and_withdrawals`) treats this as success, the withdrawal UTXO is never persisted, and every subsequent attempt to process that withdrawal fails permanently — locking the corresponding bridged BTC in the vault.

---

### Finding Description

`update_withdrawal_utxo_from_citrea_withdrawal` in `core/src/database/verifier.rs` executes:

```sql
UPDATE withdrawals
SET withdrawal_utxo_txid = $2,
    withdrawal_utxo_vout = $3,
    withdrawal_batch_proof_bitcoin_block_height = $4
WHERE idx = $1
```

via `execute_query_with_tx!(…, execute)`, which discards the `PgQueryResult` (rows-affected count) and unconditionally returns `Ok(())`. [1](#0-0) 

The sibling function `update_replacement_deposit_move_txid` in the same file uses `RETURNING idx` + `fetch_optional` and explicitly errors when no row is matched:

```rust
if query.is_none() {
    return Err(eyre::eyre!("Replacement move txid not found: {}", idx).into());
}
``` [2](#0-1) 

The `withdrawals` table row for a given `idx` is created by `upsert_move_to_vault_txid_from_citrea_deposit` (the deposit path). The withdrawal-UTXO update is a separate step that must find that pre-existing row. If the row is absent — because the deposit event for that `idx` has not yet been processed — the UPDATE silently touches zero rows and the function returns success. [3](#0-2) 

The caller `update_citrea_deposit_and_withdrawals` processes deposits first, then withdrawals, within the same L2-height window. However, if a withdrawal event falls into an earlier height window than its corresponding deposit event (e.g., due to a Citrea reorg, a missed deposit batch, or a height-range boundary split), the withdrawal update is attempted before the deposit row exists. [4](#0-3) 

Because `get_last_withdrawal_idx` queries `MAX(idx) WHERE withdrawal_utxo_txid IS NOT NULL`, a silently-failed update does not advance the high-water mark: [5](#0-4) 

On every subsequent sync cycle, `collect_withdrawal_utxos` re-fetches the same withdrawal starting from the same `last_withdrawal_idx`, the update is retried, silently fails again, and the cycle repeats indefinitely.

---

### Impact Explanation

Both downstream consumers of the withdrawal UTXO fail when the row is absent or the UTXO fields are NULL:

- **Operator payout path** (`operator.rs:589-596`): `get_withdrawal_utxo_from_citrea_withdrawal` returns an error, blocking `operator.withdraw`. [6](#0-5) 

- **Optimistic payout path** (`verifier.rs:1647-1659`): `sign_optimistic_payout` calls the same getter and errors, preventing verifier co-signing. [7](#0-6) 

The result is a **permanent liveness failure** for the affected withdrawal index: the user's bridged BTC remains locked in the move-to-vault UTXO with no code path able to release it, satisfying the "permanent lock of bridged BTC" criterion in the Allowed Impact Gate.

---

### Likelihood Explanation

The trigger requires the withdrawal event for index `N` to be processed in a sync window that precedes the deposit event for the same `N`. This can occur when:

1. A Citrea L2 reorg removes the deposit block but not the withdrawal block.
2. The height-range boundary in `update_citrea_deposit_and_withdrawals` splits a deposit and its paired withdrawal across two consecutive calls (deposit at `l2_height_end`, withdrawal at `l2_height_end - 1` of the next window).
3. A transient RPC failure causes the deposit batch to be skipped while the withdrawal batch succeeds.

These are non-negligible operational conditions on a live bridge, making likelihood **medium**.

---

### Recommendation

Mirror the pattern used by `update_replacement_deposit_move_txid`: add `RETURNING idx` to the UPDATE statement and return an error when no row is matched.

```rust
let query = sqlx::query(
    "UPDATE withdrawals
     SET withdrawal_utxo_txid = $2,
         withdrawal_utxo_vout = $3,
         withdrawal_batch_proof_bitcoin_block_height = $4
     WHERE idx = $1
     RETURNING idx",
)
// ... binds ...
.fetch_optional(/* connection */)
.await?;

if query.is_none() {
    return Err(eyre::eyre!(
        "Withdrawal row not found for citrea_idx {}: deposit must be processed first",
        citrea_idx
    ).into());
}
Ok(())
```

This converts the silent no-op into a hard error that surfaces immediately in `update_citrea_deposit_and_withdrawals`, allowing the operator to detect and remediate the ordering issue before the withdrawal is permanently lost.

---

### Proof of Concept

1. Start a Clementine verifier/operator pair against a mock Citrea client.
2. Insert a withdrawal UTXO event at height H for `idx = 5` **without** first inserting a deposit move-txid for `idx = 5`.
3. Trigger `update_citrea_deposit_and_withdrawals` with `l2_height_end = H`.
4. Observe that `update_withdrawal_utxo_from_citrea_withdrawal(5, utxo, H)` returns `Ok(())`.
5. Call `get_withdrawal_utxo_from_citrea_withdrawal(5)` — it returns `Err("Withdrawal utxo is not set for deposit 5")`.
6. Confirm that `get_last_withdrawal_idx()` still returns `None` (or the previous max), so the next sync cycle re-fetches and silently fails again.
7. Now insert the deposit for `idx = 5` and re-run the sync — the update succeeds, confirming the root cause is the missing existence check, not a schema issue. [1](#0-0) [2](#0-1) [4](#0-3)

### Citations

**File:** core/src/database/verifier.rs (L35-48)
```rust
    pub async fn get_last_withdrawal_idx(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
    ) -> Result<Option<u32>, BridgeError> {
        let query = sqlx::query_as::<_, (i32,)>(
            "SELECT COALESCE(MAX(idx), -1) FROM withdrawals WHERE withdrawal_utxo_txid IS NOT NULL",
        );
        let result = execute_query_with_tx!(self.connection, tx, query, fetch_one)?;
        if result.0 == -1 {
            Ok(None)
        } else {
            Ok(Some(result.0 as u32))
        }
    }
```

**File:** core/src/database/verifier.rs (L50-67)
```rust
    pub async fn upsert_move_to_vault_txid_from_citrea_deposit(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        move_to_vault_txid: &Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "INSERT INTO withdrawals (idx, move_to_vault_txid)
             VALUES ($1, $2)
             ON CONFLICT (idx) DO UPDATE
             SET move_to_vault_txid = $2",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(*move_to_vault_txid));

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L85-106)
```rust
    pub async fn update_replacement_deposit_move_txid(
        &self,
        tx: DatabaseTransaction<'_>,
        idx: u32,
        new_move_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET move_to_vault_txid = $2
             WHERE idx = $1
             RETURNING idx",
        )
        .bind(i32::try_from(idx).wrap_err("Failed to convert idx to i32")?)
        .bind(TxidDB(new_move_txid))
        .fetch_optional(tx.deref_mut())
        .await?;

        if query.is_none() {
            return Err(eyre::eyre!("Replacement move txid not found: {}", idx).into());
        }
        Ok(())
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

**File:** core/src/verifier.rs (L1647-1659)
```rust
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
            .await?;

        if withdrawal_utxo != input_outpoint {
            return Err(eyre::eyre!(
                "Withdrawal utxo is not correct: {:?} != {:?}",
                withdrawal_utxo,
                input_outpoint
            )
            .into());
        }
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

**File:** core/src/operator.rs (L589-596)
```rust
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
```
