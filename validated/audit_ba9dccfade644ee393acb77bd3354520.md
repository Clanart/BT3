### Title
Unbounded Sequential RPC Loop in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Can Stall Verifier Block Processing and Cause Missed Challenge Windows - (File: `core/src/citrea.rs`)

### Summary

`CitreaClient::collect_deposit_move_txids` and `CitreaClient::collect_withdrawal_utxos` iterate over the Citrea bridge contract's deposit/withdrawal arrays one element at a time with no upper-bound guard. Each iteration fires a separate blocking RPC call. When the verifier restarts after downtime, or when a large batch of deposits/withdrawals accumulates between two L1 block processing cycles, the resulting O(N) sequential RPC fan-out can stall `update_citrea_deposit_and_withdrawals` long enough for the verifier to miss the on-chain challenge window for a malicious kickoff, allowing an operator to steal bridged BTC.

### Finding Description

`collect_deposit_move_txids` (lines 420–456) and `collect_withdrawal_utxos` (lines 458–496) in `core/src/citrea.rs` both use an unbounded `loop` that increments an index and calls the Citrea contract (`depositTxIds(idx)` / `withdrawalUTXOs(idx)`) once per entry, terminating only when the contract reverts with an out-of-bounds error:

```rust
loop {
    let deposit_txid = self
        .contract
        .depositTxIds(U256::from(start_idx))
        .block(...)
        .call()
        .await;
    match deposit_txid {
        Err(e) if e.to_string().contains("execution reverted") => { break; }
        ...
    }
    move_txids.push((start_idx as u64, move_txid));
    start_idx += 1;
}
``` [1](#0-0) [2](#0-1) 

Both functions are called unconditionally from `update_citrea_deposit_and_withdrawals` in `core/src/verifier.rs` on every L1 block the verifier processes: [3](#0-2) 

The `CitreaClient` is constructed with a per-request timeout of 60 seconds (defaulting to `Duration::from_secs(60)`): [4](#0-3) 

There is no maximum-batch-size guard anywhere in either loop. The only termination condition is the contract reverting on an out-of-bounds index.

### Impact Explanation

If N new deposits or withdrawals have accumulated since the verifier's last processed index, the verifier must complete N sequential Citrea RPC calls before it can finish processing the current L1 block. At 60 s timeout per call, N = 100 entries already risks a 100-minute stall. During this stall:

1. The verifier's block-processing loop is blocked inside `update_citrea_deposit_and_withdrawals`.
2. The verifier cannot observe new Bitcoin blocks, detect malicious kickoff transactions, or submit challenge transactions.
3. If the stall exceeds `operator_challenge_timeout_timelock` blocks, the challenge window closes and the operator's fraudulent payout becomes final, permanently losing the bridged BTC for the affected deposit.

The `withdrawals` table and the `kickoff_machines` state machines that depend on timely withdrawal-UTXO data are also left stale, breaking the payout-detection invariant in `update_finalized_payouts`. [5](#0-4) 

### Likelihood Explanation

- Any user can create legitimate deposits (by sending BTC to the bridge address) or withdrawals (by holding cBTC on Citrea). No privileged access is required.
- A verifier restart after even modest downtime (e.g., a few hours during which hundreds of deposits occurred) triggers the full catch-up scan.
- The attack is amplified if the Citrea node is under load or geographically distant, increasing per-call latency.
- The `last_deposit_idx` / `last_withdrawal_idx` checkpointing only helps in steady state; it does not bound the batch size after a gap. [6](#0-5) [7](#0-6) 

### Recommendation

1. **Add a per-call batch cap**: Introduce a `MAX_BATCH_SIZE` constant (e.g., 500) and break the loop after fetching that many entries per invocation, resuming from the saved index on the next block.
2. **Parallelize RPC calls**: Replace the sequential loop with concurrent fetches (e.g., `futures::future::join_all`) up to the batch cap.
3. **Enforce a hard timeout on the entire collection phase**: Wrap `collect_deposit_move_txids` / `collect_withdrawal_utxos` in `tokio::time::timeout` so a slow Citrea node cannot stall block processing indefinitely.
4. **Persist partial progress**: If a batch cap is hit, persist the last processed index so the next block-processing cycle continues from where it left off rather than re-fetching.

### Proof of Concept

1. Deploy the bridge on a test network.
2. Register 1 000 deposits on Citrea (each requires a valid move-tx on Bitcoin, but can be done with small amounts).
3. Stop the verifier for several L1 blocks.
4. Restart the verifier. Observe that `update_citrea_deposit_and_withdrawals` fires 1 000 sequential `depositTxIds(idx)` calls before the verifier can process any new Bitcoin block.
5. While the verifier is stalled, broadcast a malicious kickoff transaction on Bitcoin.
6. Confirm that the verifier does not submit a challenge transaction within `operator_challenge_timeout_timelock` blocks because its block-processing loop is blocked.
7. After the timeout, the operator's fraudulent payout is final. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** core/src/citrea.rs (L397-401)
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

**File:** core/src/verifier.rs (L2283-2310)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
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
```
