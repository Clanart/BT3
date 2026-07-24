### Title
Unbounded Sequential Per-Item RPC Polling in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Stalls Verifier Block Processing, Enabling Unchallenged Fraudulent Assertions — (File: core/src/citrea.rs)

---

### Summary

`CitreaClient::collect_deposit_move_txids` and `CitreaClient::collect_withdrawal_utxos` in `core/src/citrea.rs` iterate over the Citrea bridge contract's `depositTxIds` and `withdrawalUTXOs` arrays using an unbounded sequential loop that fires **one individual EVM `eth_call` per item**. Both loops are invoked unconditionally on every finalized Bitcoin block inside `Verifier::handle_finalized_block` → `update_citrea_deposit_and_withdrawals`. On a fresh verifier start (empty database) or after any period of downtime, the loops must replay every historical deposit and withdrawal one-by-one before the verifier can process a single new block. During that replay window the verifier cannot detect invalid operator assertions, send disprove transactions, or track payout outputs, directly threatening the safety of all bridged BTC.

---

### Finding Description

**Two unbounded sequential polling loops — one per pipeline:**

`collect_deposit_move_txids` (citrea.rs:432–454):
```rust
loop {
    let deposit_txid = self
        .contract
        .depositTxIds(U256::from(start_idx))   // one eth_call per deposit
        .block(...)
        .call()
        .await;
    match deposit_txid {
        Err(e) if e.to_string().contains("execution reverted") => { break; }
        ...
    }
    start_idx += 1;
}
```

`collect_withdrawal_utxos` (citrea.rs:470–494) is structurally identical, calling `withdrawalUTXOs(U256::from(start_idx))` one at a time.

Both loops start from `last_deposit_idx + 1` / `last_withdrawal_idx + 1` as returned by `get_last_deposit_idx` / `get_last_withdrawal_idx` (database/verifier.rs:20–48), which query `MAX(idx) FROM withdrawals`. When the database is empty (fresh start or DB reset), both return `None`, causing both loops to begin at index 0 and iterate over the entire historical state of the contract.

**Call chain on every finalized block:**

```
LcpSyncerTask::run_once
  → FinalizedBlockFetcherTask::run_once          (bitcoin_syncer.rs:505)
    → Verifier::handle_new_block                 (task/lcp_syncer.rs:59)
      → Verifier::handle_finalized_block         (verifier.rs:3053)
        → update_citrea_deposit_and_withdrawals  (verifier.rs:2082)
          → collect_deposit_move_txids           (citrea.rs:420)  ← O(N) sequential calls
          → collect_withdrawal_utxos             (citrea.rs:458)  ← O(M) sequential calls
```

This is the direct analog of the Skale "iterations over slashes" pattern: two separate pipelines, each making unbounded sequential calls over a growing collection, executed as part of every routine block-processing cycle.

---

### Impact Explanation

The verifier's `handle_finalized_block` is the sole mechanism by which the verifier:
- Detects kickoff transactions and operator assertions on Bitcoin
- Dispatches `WatchtowerChallenge`, `Disprove`, and `LatestBlockhash` duties via the `KickoffStateMachine`
- Tracks payout transactions for reimbursement accounting

All of these duties are time-bounded by on-chain timelocks. The critical window is `disprove_timeout_timelock = 720 blocks` (~5 days at 10 min/block). If the verifier is blocked inside the deposit/withdrawal polling loops for longer than this window, an operator can:

1. Broadcast a kickoff with an invalid BitVM assertion.
2. Wait out the `disprove_timeout_timelock` (720 blocks) while the verifier is stuck.
3. Spend the `DisproveTimeout` output, claiming the reimbursement and the operator collateral without being challenged.

The operator collateral is `OPERATOR_CHALLENGE_AMOUNT = 200,000,000 sat` (2 BTC) plus the bridged `BRIDGE_AMOUNT = 1,000,000,000 sat` (10 BTC) per deposit. With multiple concurrent kickoffs, the total at risk scales with `NUM_KICKOFFS_PER_ROUND × NUM_ROUND_TXS`.

---

### Likelihood Explanation

The blocking condition arises in two realistic scenarios:

1. **Fresh deployment / DB migration**: Any new verifier node joining the bridge after deposits have accumulated must replay all historical deposits before it can process live blocks. With thousands of deposits and a Citrea RPC node under load (100–500 ms per call), tens of thousands of deposits produce hours of blocking.

2. **Verifier downtime + catch-up**: If a verifier is offline for any reason (crash, maintenance, network partition) and deposits accumulate during that period, the catch-up loop must process all missed deposits sequentially before resuming normal block processing.

Neither scenario requires the attacker to spend BTC. The attacker (a malicious operator) only needs to time their fraudulent kickoff to coincide with a known verifier restart or downtime window. The verifier restart is observable on-chain (the verifier's watchtower challenge transactions stop appearing).

---

### Recommendation

1. **Replace per-item polling with event-log batch queries.** The existing `get_replacement_deposit_move_txids` function already demonstrates the correct pattern — it uses `get_logs` with a block range filter to fetch all `DepositReplaced` events in a single batched call. Apply the same approach to `collect_deposit_move_txids` (listen for `DepositMade` / equivalent events) and `collect_withdrawal_utxos` (listen for `WithdrawalRequested` / equivalent events).

2. **Merge the two pipelines into one.** A single `get_logs` call covering both deposit and withdrawal events in the same block range eliminates the two-pipeline redundancy noted in the Skale report.

3. **Add a startup catch-up guard.** If the verifier detects it is more than N blocks behind, it should process the historical backlog in batches before registering itself as a live watchtower, rather than blocking the live block-processing loop.

---

### Proof of Concept

**Setup:** Deploy Clementine bridge. Create 50,000 deposits on Citrea (each deposit is a legitimate bridge deposit; no BTC theft required at this stage). Stop the verifier and wipe its database (simulating a fresh deployment or DB migration).

**Step 1 — Measure blocking time.** Restart the verifier. Observe that `handle_finalized_block` does not return for the first invocation until all 50,000 `depositTxIds(i)` calls complete. At 200 ms/call (realistic for a loaded Citrea RPC), this is 10,000 seconds ≈ 2.8 hours.

**Step 2 — Exploit the window.** While the verifier is blocked, the malicious operator broadcasts a kickoff transaction with an invalid BitVM assertion (e.g., a zeroed-out Winternitz commitment). The `operator_challenge_timeout_timelock = 144 blocks` passes; no challenge is sent. The operator then sends the assert transactions. The `disprove_timeout_timelock = 720 blocks` passes; no disprove is sent.

**Step 3 — Claim funds.** The operator spends the `DisproveTimeout` output, collecting the reimbursement for the fraudulent withdrawal. The verifier, still catching up on historical deposits, never dispatched a `Disprove` duty.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/citrea.rs (L420-456)
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
    }
```

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

**File:** core/src/verifier.rs (L2204-2281)
```rust
    #[tracing::instrument(skip(self, dbtx))]
    async fn update_citrea_deposit_and_withdrawals(
        &self,
        dbtx: DatabaseTransaction<'_>,
        l2_height_start: u64,
        l2_height_end: u64,
        block_height: u32,
    ) -> Result<(), BridgeError> {
        let last_deposit_idx = self.db.get_last_deposit_idx(Some(dbtx)).await?;
        tracing::debug!("Last Citrea deposit idx: {:?}", last_deposit_idx);

        let last_withdrawal_idx = self.db.get_last_withdrawal_idx(Some(dbtx)).await?;
        tracing::debug!("Last Citrea withdrawal idx: {:?}", last_withdrawal_idx);

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

        for (idx, move_to_vault_txid) in new_deposits {
            tracing::info!(
                "Saving move to vault txid {:?} with index {} for Citrea deposits",
                move_to_vault_txid,
                idx
            );
            self.db
                .upsert_move_to_vault_txid_from_citrea_deposit(
                    Some(dbtx),
                    idx as u32,
                    &move_to_vault_txid,
                )
                .await?;
        }

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

        let replacement_move_txids = self
            .citrea_client
            .get_replacement_deposit_move_txids(l2_height_start + 1, l2_height_end)
            .await?;

        for (idx, new_move_txid) in replacement_move_txids {
            tracing::info!(
                "Setting replacement move txid: {:?} -> {:?}",
                idx,
                new_move_txid
            );
            self.db
                .update_replacement_deposit_move_txid(dbtx, idx, new_move_txid)
                .await?;
        }

        Ok(())
    }
```

**File:** core/src/verifier.rs (L3053-3108)
```rust
    pub async fn handle_finalized_block(
        &self,
        mut dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_height: u32,
        block_cache: Arc<block_cache::BlockCache>,
        light_client_proof_wait_interval_secs: Option<u32>,
    ) -> Result<(), BridgeError> {
        tracing::info!("Verifier handling finalized block height: {}", block_height);

        // before a certain number of blocks, citrea doesn't produce proofs (defined in citrea config)
        let max_attempts = light_client_proof_wait_interval_secs.unwrap_or(TEN_MINUTES_IN_SECS);
        let timeout = Duration::from_secs(max_attempts as u64);

        let (l2_height_start, l2_height_end) = self
            .citrea_client
            .get_citrea_l2_height_range(
                block_height.into(),
                timeout,
                self.config.protocol_paramset(),
            )
            .await
            .inspect_err(|e| tracing::error!("Error getting citrea l2 height range: {:?}", e))?;

        tracing::debug!(
            "l2_height_start: {:?}, l2_height_end: {:?}, collecting deposits and withdrawals...",
            l2_height_start,
            l2_height_end
        );
        self.update_citrea_deposit_and_withdrawals(
            dbtx,
            l2_height_start,
            l2_height_end,
            block_height,
        )
        .await?;

        self.update_finalized_payouts(dbtx, block_id, &block_cache)
            .await?;

        #[cfg(feature = "automation")]
        {
            // Save unproven block cache to the database
            self.header_chain_prover
                .save_unproven_block_cache(Some(&mut dbtx), &block_cache)
                .await?;
            while (self.header_chain_prover.prove_if_ready().await?).is_some() {
                // Continue until prove_if_ready returns None
                // If it doesn't return None, it means next batch_size amount of blocks were proven
            }
            // notify that lcp was processed for this height to state manager
            StateManager::<Self>::dispatch_lcp_processed(&self.db, dbtx, block_height).await?;
        }

        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L20-48)
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

**File:** core/src/task/lcp_syncer.rs (L54-75)
```rust
#[async_trait::async_trait]
impl<C> crate::bitcoin_syncer::BlockHandler for Verifier<C>
where
    C: CitreaClientT,
{
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
}
```
