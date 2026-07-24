### Title
Unbounded Sequential RPC Calls in `collect_deposit_move_txids` and `collect_withdrawal_utxos` Stall Verifier Block Processing, Potentially Causing Missed Watchtower Challenge Windows — (File: `core/src/citrea.rs`)

---

### Summary

`CitreaClient::collect_deposit_move_txids` and `CitreaClient::collect_withdrawal_utxos` loop indefinitely, issuing one sequential Citrea EVM RPC call per deposit or withdrawal with no upper bound, no pagination, and no aggregate timeout. Both functions are called on every Bitcoin block processed by the verifier via `update_citrea_deposit_and_withdrawals`. A large backlog of new deposits or withdrawals blocks the verifier's entire block-processing task for up to N × (per-call timeout) seconds, preventing timely watchtower challenge submission and potentially allowing a malicious operator to escape a challenge and retain bridged BTC.

---

### Finding Description

`collect_deposit_move_txids` iterates from `last_deposit_idx + 1` upward, calling `contract.depositTxIds(U256::from(start_idx))` once per index until the contract reverts:

```rust
// core/src/citrea.rs  lines 432-454
loop {
    let deposit_txid = self
        .contract
        .depositTxIds(U256::from(start_idx))
        .block(...)
        .call()
        .await;
    match deposit_txid {
        Err(e) if e.to_string().contains("execution reverted") => { break; }
        Err(e) => return Err(e.into()),
        Ok(_) => {}
    }
    // ... push result
    start_idx += 1;
}
```

`collect_withdrawal_utxos` is structurally identical, calling `contract.withdrawalUTXOs(U256::from(start_idx))` in the same unbounded loop.

Both functions are invoked unconditionally on every Bitcoin block inside `update_citrea_deposit_and_withdrawals`:

```rust
// core/src/verifier.rs  lines 2218-2231
let new_deposits = self
    .citrea_client
    .collect_deposit_move_txids(last_deposit_idx, l2_height_end)
    .await?;

let new_withdrawals = self
    .citrea_client
    .collect_withdrawal_utxos(last_withdrawal_idx, l2_height_end)
    .await?;
```

The `HttpClientBuilder` sets a per-call `request_timeout` of 60 seconds. There is no aggregate timeout wrapping either loop. If N new deposits or withdrawals have accumulated since the last sync, the verifier is blocked for up to N × 60 s before it can process the next Bitcoin block.

Additionally, `get_last_deposit_idx` — which determines the starting index for `collect_deposit_move_txids` — queries `SELECT COALESCE(MAX(idx), -1) FROM withdrawals` without filtering on deposit-specific columns (`move_to_vault_txid IS NOT NULL`). Because the `withdrawals` table stores both deposit rows and withdrawal rows under the same `idx` column, if withdrawal indices are numerically higher than deposit indices, this function returns a withdrawal index as the "last deposit index," causing `collect_deposit_move_txids` to start too high and silently skip unsynced deposits.

---

### Impact Explanation

The verifier's block-processing task is sequential. `update_citrea_deposit_and_withdrawals` runs inside that task on every Bitcoin block. While it is blocked fetching N items one-by-one, the verifier cannot:

- Advance its state machines to detect kickoff transactions on-chain.
- Dispatch `Duty::AddRelevantTxsToTxSenderIfChallenged`.
- Submit the watchtower challenge transaction within the `time_to_send_watchtower_challenge` window (216 Bitcoin blocks, ~1.5 days in production config).

If the verifier misses that window, the operator can spend the `WatchtowerChallengeTimeout` UTXO, closing the kickoff state machine without a valid challenge. The operator then proceeds through the reimbursement flow unchallenged, retaining bridged BTC that should have been disputed.

The `get_last_deposit_idx` bug compounds this: if deposits are silently skipped, the verifier's DB never records the corresponding `move_to_vault_txid`, so `sign_optimistic_payout` and withdrawal validation will fail for those deposit IDs, permanently blocking the withdrawal flow for affected users.

---

### Likelihood Explanation

- **Deposit path**: Each deposit requires locking `bridge_amount` (10 BTC in production). Accumulating enough deposits to block the verifier for hours is expensive but not impossible for a well-funded attacker targeting a specific challenge window.
- **Withdrawal path**: Withdrawals are initiated by ordinary L2 users. A surge of legitimate withdrawals (e.g., during a market event) can trigger the same stall without any attacker spending BTC.
- **Restart scenario**: If the verifier crashes and restarts after a period of bridge activity, `last_deposit_idx` / `last_withdrawal_idx` may be far behind the current contract state. The catch-up loop then makes hundreds or thousands of sequential RPC calls before the verifier can process any new Bitcoin block, creating a deterministic liveness gap on every restart.
- No privileged access is required to trigger any of these paths.

---

### Recommendation

1. **Batch reads**: Query the Citrea contract for a range of indices in a single call (if the contract supports it), or fetch items in fixed-size chunks (e.g., 50 at a time) with a configurable `max_items_per_sync` cap.
2. **Aggregate timeout**: Wrap each call to `collect_deposit_move_txids` / `collect_withdrawal_utxos` with `tokio::time::timeout` so a single slow Citrea node cannot block the verifier indefinitely.
3. **Fix `get_last_deposit_idx`**: Change the SQL to `SELECT COALESCE(MAX(idx), -1) FROM withdrawals WHERE move_to_vault_txid IS NOT NULL` so it returns the true last deposit index rather than the maximum of all rows.
4. **Decouple Citrea sync from block processing**: Run the Citrea sync in a separate background task so a slow Citrea RPC cannot stall Bitcoin block processing and state-machine advancement.

---

### Proof of Concept

**Scenario — withdrawal surge stalls verifier during an active challenge:**

1. Operator broadcasts a kickoff transaction at Bitcoin block height H. The verifier's state machine sets a `Matcher::BlockHeight(H + 216)` deadline for `KickoffEvent::TimeToSendWatchtowerChallenge`.
2. Between L2 heights corresponding to Bitcoin blocks H and H+10, N users initiate withdrawals on Citrea (N = 500, achievable during normal bridge usage).
3. At Bitcoin block H+10, the verifier calls `update_citrea_deposit_and_withdrawals`. `collect_withdrawal_utxos` issues 500 sequential RPC calls. At even 2 s per call, this takes ~17 minutes, blocking the verifier from processing Bitcoin blocks H+11 through H+12 (≈20 minutes of Bitcoin time).
4. If the verifier is also restarting (e.g., after a crash at block H), `collect_deposit_move_txids` additionally replays all historical deposits from index 0, compounding the delay.
5. If the cumulative delay pushes past block H+216, the operator broadcasts `WatchtowerChallengeTimeout`, spending the challenge UTXO before the verifier can submit its watchtower challenge. The kickoff state machine transitions to `Closed` without a valid challenge, and the operator retains the bridged BTC.

**Corrupted value**: the verifier's `next_height_to_process` lags behind the Bitcoin tip by the number of blocks skipped during the RPC stall, causing all time-sensitive matchers (challenge deadlines, assert timeouts) to fire late or not at all. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/citrea.rs (L396-400)
```rust

        let client = HttpClientBuilder::default()
            .request_timeout(timeout.unwrap_or(Duration::from_secs(60)))
            .build(citrea_rpc_url)
            .wrap_err("Failed to create Citrea RPC client")?;
```

**File:** core/src/citrea.rs (L432-454)
```rust
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
```

**File:** core/src/citrea.rs (L470-494)
```rust
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
```

**File:** core/src/verifier.rs (L2218-2231)
```rust
        let new_deposits = self
            .citrea_client
            .collect_deposit_move_txids(last_deposit_idx, l2_height_end)
            .await?;
        tracing::debug!("New deposits received from Citrea: {:?}", new_deposits);

        let new_withdrawals = self
            .citrea_client
            .collect_withdrawal_utxos(last_withdrawal_idx, l2_height_end)
            .await?;
        tracing::debug!(
            "New withdrawals received from Citrea: {:?}",
            new_withdrawals
        );
```

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

**File:** core/src/states/kickoff.rs (L382-388)
```rust
                    self.matchers.insert(
                        Matcher::BlockHeight(
                            self.kickoff_height
                                + context.config.time_to_send_watchtower_challenge as u32,
                        ),
                        KickoffEvent::TimeToSendWatchtowerChallenge,
                    );
```
