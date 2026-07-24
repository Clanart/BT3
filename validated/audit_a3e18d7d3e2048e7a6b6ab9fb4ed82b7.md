### Title
`get_last_deposit_idx` Queries Wrong Row Set, Causing Verifier to Skip Unprocessed Deposits — (File: `core/src/database/verifier.rs`)

---

### Summary

`Database::get_last_deposit_idx` computes the "last synced deposit index" by running `SELECT COALESCE(MAX(idx), -1) FROM withdrawals` with **no filter**. Because the `withdrawals` table stores rows for both deposit-sync events (`move_to_vault_txid`) and withdrawal-sync events (`withdrawal_utxo_txid`), the function can return an `idx` that belongs to a withdrawal row whose corresponding deposit has never been processed. The caller (`collect_deposit_move_txids`) uses this value as the exclusive lower bound for the next Citrea deposit scan, so every deposit whose Citrea index falls between the true last-processed deposit and the spuriously high returned value is permanently skipped.

---

### Finding Description

`core/src/database/verifier.rs` lines 20-31:

```rust
pub async fn get_last_deposit_idx(
    &self,
    tx: Option<DatabaseTransaction<'_>>,
) -> Result<Option<u32>, BridgeError> {
    let query = sqlx::query_as::<_, (i32,)>(
        "SELECT COALESCE(MAX(idx), -1) FROM withdrawals"   // ← no WHERE clause
    );
    ...
}
```

The `withdrawals` table is a shared store. Rows are inserted by two independent paths:

| Path | Column set | Trigger |
|---|---|---|
| Deposit syncer | `idx`, `move_to_vault_txid` | `upsert_move_to_vault_txid_from_citrea_deposit` |
| Withdrawal syncer | `idx`, `withdrawal_utxo_txid` | withdrawal UTXO tracking |

Both paths share the same `idx` column (the Citrea deposit index). The deposit syncer and withdrawal syncer run concurrently and independently. If the withdrawal syncer inserts a row for Citrea deposit index `N` before the deposit syncer has processed deposits `M+1 … N-1`, then `MAX(idx) FROM withdrawals` returns `N`, even though deposits `M+1 … N-1` have never been stored.

`collect_deposit_move_txids` in `core/src/citrea.rs` (lines 420-455) uses the returned value directly as the start of the next scan:

```rust
let mut start_idx = match last_deposit_idx {
    Some(idx) => idx + 1,   // starts AFTER the returned max
    None => 0,
};
```

With `last_deposit_idx = N`, the scan begins at `N+1`, permanently skipping deposits `M+1 … N`.

The correct query for `get_last_deposit_idx` should mirror `get_last_withdrawal_idx` and filter to rows that actually represent a processed deposit:

```sql
SELECT COALESCE(MAX(idx), -1) FROM withdrawals WHERE move_to_vault_txid IS NOT NULL
```

---

### Impact Explanation

The verifier relies on the deposit-move-txid mapping to:
1. Validate that a withdrawal request corresponds to a real, finalized deposit.
2. Decide whether to sign reimbursement transactions.
3. Detect and challenge fraudulent operator payouts.

Deposits whose `move_to_vault_txid` is never stored are invisible to the verifier's withdrawal-validation logic. An operator can submit a payout for one of these ghost deposits; the verifier has no local record to check against and cannot raise a challenge. This breaks the watchtower safety guarantee for the affected deposits, exposing bridged BTC to theft without any on-chain challenge.

---

### Likelihood Explanation

The withdrawal syncer and deposit syncer are background tasks that run concurrently. In any deployment where withdrawals arrive for deposits that the deposit syncer has not yet caught up to (e.g., after a restart, a slow Bitcoin node, or a gap in Citrea block processing), the withdrawal syncer will insert rows with higher `idx` values before the deposit syncer does. This is a normal operational condition, not an edge case.

---

### Recommendation

Change the SQL in `get_last_deposit_idx` to filter only rows where the deposit has actually been recorded:

```rust
// core/src/database/verifier.rs
let query = sqlx::query_as::<_, (i32,)>(
    "SELECT COALESCE(MAX(idx), -1) FROM withdrawals WHERE move_to_vault_txid IS NOT NULL"
);
```

This mirrors the existing `get_last_withdrawal_idx` pattern and ensures the deposit scan always resumes from the correct boundary.

---

### Proof of Concept

1. Citrea has deposits at indices 0–9 finalized on-chain.
2. The deposit syncer has processed indices 0–4 (`move_to_vault_txid` set for rows 0–4).
3. The withdrawal syncer processes a withdrawal for deposit index 8, inserting a row with `idx=8`, `withdrawal_utxo_txid=<txid>`, `move_to_vault_txid=NULL`.
4. `get_last_deposit_idx` executes `SELECT MAX(idx) FROM withdrawals` → returns **8**.
5. `collect_deposit_move_txids` sets `start_idx = 9`, scanning only index 9 onward.
6. Deposits 5, 6, 7 are **never fetched**; their `move_to_vault_txid` is never stored.
7. An operator submits fraudulent payouts for deposits 5–7. The verifier has no record of these deposits and cannot challenge them. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/src/database/verifier.rs (L20-31)
```rust
    pub async fn get_last_deposit_idx(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
    ) -> Result<Option<u32>, BridgeError> {
        let query = sqlx::query_as::<_, (i32,)>("SELECT COALESCE(MAX(idx), -1) FROM withdrawals");
        let result = execute_query_with_tx!(self.connection, tx, query, fetch_one)?;
        if result.0 == -1 {
            Ok(None)
        } else {
            Ok(Some(result.0 as u32))
        }
    }
```

**File:** core/src/database/verifier.rs (L33-48)
```rust
    /// Returns the last withdrawal index where withdrawal_utxo_txid exists.
    /// If no withdrawals with UTXOs exist, returns None.
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

**File:** core/src/citrea.rs (L420-455)
```rust
    async fn collect_deposit_move_txids(
        &self,
        last_deposit_idx: Option<u32>,
        to_height: u64,
    ) -> Result<Vec<(u64, Txid)>, BridgeError> {
        let mut move_txids = vec![];

        let mut start_idx = match last_deposit_idx {
            Some(idx) => idx + 1,
            None => 0,
        };

        loop {
            let deposit_txid = self
                .contract
                .depositTxIds(U256::from(start_idx))
                .block(BlockId::Number(BlockNumberOrTag::Number(to_height)))
                .call()
                .await;
            match deposit_txid {
                Err(e) if e.to_string().contains("execution reverted") => {
                    tracing::trace!("Deposit txid not found for index, error: {:?}", e);
                    break;
                }
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            tracing::info!("Deposit txid found for index: {:?}", deposit_txid);

            let deposit_txid = deposit_txid.expect("Failed to get deposit txid");
            let move_txid = Txid::from_slice(deposit_txid._0.as_ref())
                .wrap_err("Failed to convert move txid to Txid")?;
            move_txids.push((start_idx as u64, move_txid));
            start_idx += 1;
        }
        Ok(move_txids)
```
