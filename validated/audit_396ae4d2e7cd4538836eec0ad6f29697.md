### Title
Unbounded Sequential RPC Loop in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Blocks Verifier Liveness on Restart - (File: `core/src/citrea.rs`)

---

### Summary

`collect_deposit_move_txids` and `collect_withdrawal_utxos` in `core/src/citrea.rs` contain unbounded loops that issue one sequential Citrea RPC call per deposit or withdrawal index. On a fresh verifier start (or after a significant lag), the loop begins at index 0 and iterates over every deposit or withdrawal ever recorded on Citrea, one call at a time. With a 60-second per-request timeout, a bridge with thousands of deposits can block `handle_finalized_block` for hours, preventing the verifier from processing any new Bitcoin blocks, signing transactions, or challenging invalid operator assertions during that window.

---

### Finding Description

`collect_deposit_move_txids` starts at `last_deposit_idx + 1` (or `0` when `None`) and loops, calling `contract.depositTxIds(U256::from(start_idx))` once per index until the contract reverts: [1](#0-0) 

`collect_withdrawal_utxos` is structurally identical: [2](#0-1) 

Both are called unconditionally inside `update_citrea_deposit_and_withdrawals`, which is called on every finalized Bitcoin block from `handle_finalized_block`: [3](#0-2) [4](#0-3) 

The Citrea HTTP client is constructed with a per-request timeout (defaulting to 60 seconds): [5](#0-4) 

There is no batch-fetch API, no pagination cap, and no overall deadline on the loop. The number of iterations grows monotonically with bridge usage and is unbounded.

---

### Impact Explanation

When a verifier node restarts with an empty database (`last_deposit_idx = None`), `collect_deposit_move_txids` begins at index 0 and must traverse every deposit ever registered on Citrea before it can return. At even modest RPC latency (100 ms/call), 10,000 deposits = ~17 minutes of blocking; at 1 s/call it is ~2.8 hours. During this entire period `handle_finalized_block` cannot complete, so:

- The verifier's state machine does not advance past the first block it tries to process.
- No new deposits or withdrawals are registered in the local DB.
- No watchtower challenges are issued against operator assertions.
- No LCP proofs are dispatched.

If all verifiers restart simultaneously (e.g., after a coordinated upgrade or infrastructure event), the bridge is fully halted for the catch-up duration. A malicious operator can observe the restart window and submit a fraudulent kickoff or assert transaction knowing no challenge will arrive within the challenge period, enabling theft of bridged BTC or operator collateral.

---

### Likelihood Explanation

- Verifier restarts are routine (upgrades, crashes, infrastructure migrations).
- The bridge is designed to accumulate deposits over time; the loop length grows with bridge adoption.
- The condition (`last_deposit_idx = None`) is triggered on every fresh DB or after a DB wipe/migration.
- An operator watching verifier uptime can detect the restart window via mempool or chain observation.

---

### Recommendation

1. **Batch-fetch with a count cap per call**: Replace the one-call-per-index loop with a contract view that returns a slice of deposits/withdrawals (e.g., `getDepositTxIds(startIdx, count)`), or use Citrea event logs with a block-range filter (as already done for `get_replacement_deposit_move_txids`).
2. **Paginate within a single `handle_finalized_block` invocation**: Process at most `MAX_BATCH` new entries per block, persisting the cursor, so catch-up is spread across multiple block-processing cycles rather than blocking a single call.
3. **Add an overall deadline**: Wrap the loop in a `tokio::time::timeout` so a slow Citrea node cannot stall the verifier indefinitely.

---

### Proof of Concept

1. Deploy the bridge on a network with 5,000 registered deposits.
2. Wipe the verifier's PostgreSQL database and restart the verifier process.
3. Observe that `handle_finalized_block` for the first Bitcoin block after restart calls `collect_deposit_move_txids(None, l2_height_end)`.
4. The loop issues 5,000 sequential `depositTxIds(idx)` calls to the Citrea RPC endpoint.
5. At 200 ms average RPC latency, the call takes ~17 minutes; no subsequent Bitcoin block is processed during this window.
6. A malicious operator submits a fraudulent `assert` transaction during this window; no verifier issues a challenge; the challenge period expires; operator collateral and bridged BTC are at risk. [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** core/src/citrea.rs (L397-400)
```rust
        let client = HttpClientBuilder::default()
            .request_timeout(timeout.unwrap_or(Duration::from_secs(60)))
            .build(citrea_rpc_url)
            .wrap_err("Failed to create Citrea RPC client")?;
```

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

**File:** core/src/verifier.rs (L2204-2231)
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
