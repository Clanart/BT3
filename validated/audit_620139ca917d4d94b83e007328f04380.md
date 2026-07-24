### Title
Unbounded Sequential RPC Loop in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Blocks Verifier Block Processing, Preventing Challenge Dispatch — (File: `core/src/citrea.rs`)

### Summary

`CitreaClient::collect_deposit_move_txids` and `collect_withdrawal_utxos` use unbounded `loop` constructs that issue one sequential RPC call per deposit/withdrawal index to the Citrea L2 contract. These functions are called synchronously inside `Verifier::handle_finalized_block`, which must complete before `StateManager::dispatch_lcp_processed` is called. If the loop stalls or takes too long (e.g., after a verifier restart when `last_deposit_idx` is `None` and thousands of historical deposits must be fetched one-by-one), the verifier's state machine never advances, challenge/disprove duties are never triggered, and a fraudulent operator assertion can go unchallenged within its time window.

### Finding Description

`collect_deposit_move_txids` starts at `start_idx = last_deposit_idx + 1` (or `0` if `None`) and loops, calling `self.contract.depositTxIds(U256::from(start_idx)).call().await` for each index until the contract reverts: [1](#0-0) 

`collect_withdrawal_utxos` is structurally identical: [2](#0-1) 

There is no batch-fetch, no pagination cap, and no overall loop timeout. Each individual call is bounded only by `citrea_request_timeout` (default 60 s), so N deposits → up to N × 60 s of sequential blocking.

Both functions are called from `update_citrea_deposit_and_withdrawals`, which holds an open `DatabaseTransaction`: [3](#0-2) 

`update_citrea_deposit_and_withdrawals` is called at the top of `handle_finalized_block`, before the critical `dispatch_lcp_processed` call that advances the verifier's state machine: [4](#0-3) 

If `update_citrea_deposit_and_withdrawals` fails or hangs, `dispatch_lcp_processed` is never reached, the `LcpSyncerTask` retries the same block, and the verifier is permanently stuck at that height.

### Impact Explanation

The verifier's challenge and disprove duties (`Duty::WatchtowerChallenge`, `Duty::VerifierDisprove`) are dispatched exclusively through the state manager, which is gated on `dispatch_lcp_processed`: [5](#0-4) 

If the verifier cannot advance past a block, it cannot issue a watchtower challenge or disprove transaction within the protocol's challenge window. A fraudulent operator assertion that goes unchallenged allows the operator to claim reimbursement for a payout they never made, draining operator collateral or bridged BTC from the bridge.

### Likelihood Explanation

The worst case is triggered on first startup or after a database reset, when `last_deposit_idx` is `None` and the loop starts from index 0, fetching every historical deposit. As the bridge accumulates deposits over its lifetime, this window grows proportionally. An attacker can also deliberately inflate the deposit count cheaply (each deposit is a fixed bridge amount, but the attacker can use the bridge legitimately) to extend the stall window before submitting a fraudulent assertion. [6](#0-5) 

### Recommendation

1. **Add a contract-level count query**: Call a `depositCount()` / `withdrawalCount()` view function once to know the upper bound, then fetch in parallel or in bounded batches.
2. **Impose a per-sync batch limit**: Cap the number of new entries fetched per `handle_finalized_block` invocation (e.g., 500), resuming from the saved `last_deposit_idx` on the next block.
3. **Decouple the fetch from block processing**: Run the Citrea sync in a separate background task so that a slow or stalled fetch does not block `dispatch_lcp_processed`.
4. **Add an overall loop timeout** wrapping the entire `collect_*` call, distinct from the per-request timeout, so a single invocation cannot block indefinitely.

### Proof of Concept

1. Deploy the bridge and process 5 000 deposits over time; `last_deposit_idx` in the DB reaches 4 999.
2. Restart the verifier with a fresh database (`last_deposit_idx = None`).
3. The verifier calls `handle_finalized_block` for the next finalized Bitcoin block.
4. `collect_deposit_move_txids` enters its loop at `start_idx = 0` and issues 5 000 sequential `depositTxIds(i).call().await` RPCs. At 1 s per call this takes ~83 minutes; at the 60 s timeout per call it could take up to 83 hours.
5. During this window, `dispatch_lcp_processed` is never called; the state manager does not advance; no `Duty::WatchtowerChallenge` or `Duty::VerifierDisprove` is dispatched.
6. An operator submits a fraudulent `assert` transaction on Bitcoin. The challenge window (a fixed number of blocks) expires with no verifier response.
7. The operator's fraudulent claim is accepted; they collect reimbursement for a payout they never made. [7](#0-6) [8](#0-7) [9](#0-8)

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
