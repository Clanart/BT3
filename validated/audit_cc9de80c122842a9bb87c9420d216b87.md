### Title
Unbounded Sequential Per-Index RPC Loop in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Stalls Verifier Finalized-Block Processing, Blocking Fraud Detection and Disprove Window — (`core/src/citrea.rs`)

---

### Summary

`CitreaClient::collect_deposit_move_txids` and `CitreaClient::collect_withdrawal_utxos` iterate over every deposit/withdrawal index one at a time, issuing a separate HTTP RPC call per index with no upper bound on the number of iterations. This is called synchronously inside `Verifier::handle_finalized_block` → `update_citrea_deposit_and_withdrawals`, which is the single-threaded entry point of the `LcpSyncerTask` background loop. When a large number of deposits or withdrawals have accumulated since the last processed index, the loop serialises thousands of 60-second-timeout RPC calls, blocking the verifier's entire finalized-block pipeline for hours. During that window the verifier cannot detect fraudulent operator assertions, cannot dispatch watchtower challenges, and cannot queue disprove transactions — all of which are time-bounded by the BitVM2 challenge window.

---

### Finding Description

`CitreaClient::collect_deposit_move_txids` (and its withdrawal counterpart) implement the following pattern:

```rust
loop {
    let deposit_txid = self
        .contract
        .depositTxIds(U256::from(start_idx))   // one HTTP call per index
        .block(...)
        .call()
        .await;
    match deposit_txid {
        Err(e) if e.to_string().contains("execution reverted") => { break; }
        Err(e) => return Err(e.into()),
        Ok(_) => {}
    }
    // ... push result, start_idx += 1
}
``` [1](#0-0) 

The loop terminates only when the contract reverts (i.e., the index is out of range). There is no batch size, no page limit, and no overall timeout wrapping the loop. The HTTP client is constructed with a per-request timeout of 60 seconds: [2](#0-1) 

Both functions are called unconditionally from `update_citrea_deposit_and_withdrawals`: [3](#0-2) 

which is called unconditionally from `handle_finalized_block`: [4](#0-3) 

`handle_finalized_block` is the `BlockHandler::handle_new_block` implementation for `Verifier`, invoked by `LcpSyncerTask` / `FinalizedBlockFetcherTask` for every finalized Bitcoin block: [5](#0-4) 

The task loop processes one block at a time and does not advance until `handle_new_block` returns. There is no timeout wrapping the call: [6](#0-5) 

The `last_deposit_idx` cursor is read from the DB at the start of each call and is only updated after the loop completes and the DB writes succeed. If the verifier is offline for any period during which N deposits accumulate, the first block processed after restart must drain all N indices in a single synchronous loop before the DB cursor advances.

---

### Impact Explanation

**Liveness → Safety escalation.** The verifier's entire finalized-block pipeline stalls for up to `N × 60 s` (per-request timeout) while the loop runs. During this period:

1. **Watchtower challenge window missed.** The verifier cannot call `send_watchtower_challenge` or `queue_txs_for_challenged_kickoff` because those duties are dispatched only after `handle_finalized_block` completes and the state manager processes the resulting events. If the stall exceeds the on-chain challenge timelock, a fraudulent operator's kickoff goes unchallenged.

2. **Disprove transaction not queued.** `send_disprove_tx` is gated on the same pipeline. A fraudulent operator assertion that expires during the stall cannot be disproved, allowing the operator to claim the bridge UTXO (10 BTC per deposit).

3. **Operator reimbursement blocked.** Verifiers must co-sign `BurnUnusedKickoffConnectors` and `ReadyToReimburse` transactions within their respective timelocks. A stalled verifier cannot do so, potentially causing honest operators to miss their reimbursement window and lose collateral.

The bridge amount per deposit is 10 BTC. A stall covering even a single active challenge window is sufficient to cause permanent loss of bridged BTC or operator collateral.

---

### Likelihood Explanation

The trigger requires that many deposits (or withdrawals) accumulate between two consecutive verifier sync points. This can happen:

- **Naturally over time**: the bridge is designed for long-running operation; a verifier restart after routine maintenance, a crash, or a network partition is expected. Even a few hours of downtime at moderate bridge usage (e.g., 100 deposits/hour) produces thousands of pending indices.
- **Adversarially**: any user can submit deposits to the Citrea bridge contract. An attacker who wants to delay verifier fraud detection can front-run a fraudulent operator kickoff by flooding the contract with deposits immediately before the kickoff, ensuring the verifier's next sync stalls for longer than the challenge window.

No privileged access is required to submit deposits. The capital cost scales with the number of deposits needed to exceed the challenge window duration divided by 60 s per RPC call.

---

### Recommendation

1. **Add a `max_batch` parameter** to `collect_deposit_move_txids` and `collect_withdrawal_utxos`. Process at most `max_batch` indices per call and return a continuation cursor so the caller can resume across multiple `handle_finalized_block` invocations.

2. **Wrap the entire loop in a deadline** (e.g., `tokio::time::timeout`) so that a single call cannot block the pipeline indefinitely regardless of the number of pending indices.

3. **Prefer event-log polling** over index-by-index contract calls. The Citrea bridge contract emits events for deposits and withdrawals; a single `eth_getLogs` call with a block range returns all events in one round trip, eliminating the O(N) sequential RPC pattern entirely.

---

### Proof of Concept

**Setup**: Bridge has been running for several days. The verifier goes offline for 2 hours for a routine upgrade. During those 2 hours, 500 deposits are made to the Citrea bridge contract. Simultaneously, a malicious operator broadcasts a kickoff transaction with a fraudulent assertion. The on-chain challenge window is 144 Bitcoin blocks (~24 hours), but the operator times the kickoff to land just as the verifier comes back online.

**Execution**:

1. Verifier restarts. `LcpSyncerTask` picks up the first missed finalized Bitcoin block.
2. `handle_finalized_block` calls `update_citrea_deposit_and_withdrawals`.
3. `collect_deposit_move_txids(last_idx=Some(0), to_height=current)` enters the loop at `start_idx = 1`.
4. The loop issues 500 sequential `depositTxIds(i)` RPC calls. Each call takes up to 60 s on a loaded node. Total stall: up to **500 × 60 s = 8.3 hours**.
5. During those 8.3 hours, `handle_finalized_block` never returns. The `LcpSyncerTask` does not advance. No `NewFinalizedBlock` events reach the state manager. No `WatchtowerChallenge` or `AddRelevantTxsToTxSenderIfChallenged` duties are dispatched.
6. The fraudulent operator's challenge window expires. No disprove transaction is broadcast. The operator claims the 10 BTC bridge UTXO.

**Corrupted value**: `last_deposit_idx` in the DB remains at `0` throughout the stall; the verifier's view of the deposit set is frozen, so all downstream fraud-detection logic operates on stale state.

### Citations

**File:** core/src/citrea.rs (L397-400)
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

**File:** core/src/verifier.rs (L2218-2227)
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
```

**File:** core/src/verifier.rs (L3082-3088)
```rust
        self.update_citrea_deposit_and_withdrawals(
            dbtx,
            l2_height_start,
            l2_height_end,
            block_height,
        )
        .await?;
```

**File:** core/src/task/lcp_syncer.rs (L59-74)
```rust
    async fn handle_new_block(
        &mut self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block: bitcoin::Block,
        height: u32,
    ) -> Result<(), BridgeError> {
        self.handle_finalized_block(
            dbtx,
            block_id,
            height,
            Arc::new(BlockCache::from_block(block, height)),
            None,
        )
        .await
    }
```

**File:** core/src/bitcoin_syncer.rs (L571-576)
```rust
                    self.handler
                        .handle_new_block(&mut dbtx, new_block_id, block, expected_next_finalized)
                        .await?;

                    expected_next_finalized += 1;
                }
```
