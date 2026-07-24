### Title
Unbounded Sequential RPC Polling in `collect_deposit_move_txids` / `collect_withdrawal_utxos` Stalls Verifier Block Processing, Enabling Missed Challenge Windows — (File: `core/src/citrea.rs`)

---

### Summary

`CitreaClient::collect_deposit_move_txids` and `CitreaClient::collect_withdrawal_utxos` iterate over the Citrea bridge contract's on-chain arrays one element at a time in an unbounded `loop`, issuing one sequential RPC call per entry. Both loops are called unconditionally inside `Verifier::update_citrea_deposit_and_withdrawals`, which is itself called on every finalized Bitcoin block inside `Verifier::handle_finalized_block`. A large backlog of unprocessed entries (e.g., after a burst of Citrea-side activity or a deliberate flood of cheap withdrawal registrations) causes the verifier to stall for the entire duration of N × RPC-latency before it can advance to the next block. If the stall exceeds the on-chain `operator_challenge_timeout_timelock`, the verifier misses its window to challenge a fraudulent kickoff, allowing an operator to claim reimbursement for an invalid withdrawal and drain bridged BTC.

---

### Finding Description

**Root cause — `core/src/citrea.rs`, lines 420–496**

`collect_deposit_move_txids` opens an unbounded `loop` and calls `contract.depositTxIds(U256::from(start_idx))` once per iteration, incrementing `start_idx` until the contract reverts:

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
    start_idx += 1;
}
```

`collect_withdrawal_utxos` is structurally identical, polling `withdrawalUTXOs(index)` one at a time. [1](#0-0) [2](#0-1) 

Neither function has a per-call batch limit, a maximum-iteration guard, or any parallelism. The `CitreaClient` is constructed with a per-request timeout (configurable via `citrea_request_timeout`, defaulting to 60 s), so N entries can consume up to N × 60 s of wall-clock time in the worst case.

**Call chain — `core/src/verifier.rs`**

Both functions are called from `update_citrea_deposit_and_withdrawals`, which is called synchronously (no independent timeout) from `handle_finalized_block` on every finalized Bitcoin block:

```
handle_finalized_block
  └─ update_citrea_deposit_and_withdrawals
       ├─ collect_deposit_move_txids(last_deposit_idx, l2_height_end)
       └─ collect_withdrawal_utxos(last_withdrawal_idx, l2_height_end)
``` [3](#0-2) [4](#0-3) 

The loops start from `last_deposit_idx + 1` / `last_withdrawal_idx + 1`, so they only process entries that arrived since the last checkpoint. However, if many entries accumulate between two consecutive finalized blocks (or while the verifier was offline), the entire backlog is drained in a single synchronous pass before the verifier can do anything else.

---

### Impact Explanation

The verifier's primary safety duty is to detect and challenge fraudulent operator kickoffs within the `operator_challenge_timeout_timelock` window. `handle_finalized_block` is the entry point for that duty. If the function is blocked inside the polling loops for longer than the challenge window, the verifier cannot dispatch a challenge transaction in time. In Clementine's N-of-N model, a single honest verifier is sufficient to block theft — but only if it can act within the window. A sustained stall across all verifiers (e.g., all share the same Citrea RPC endpoint, or the Citrea network itself is congested) removes that protection and allows an operator to finalize a fraudulent reimbursement, draining bridged BTC from the vault. [5](#0-4) 

---

### Likelihood Explanation

Registering a withdrawal UTXO on Citrea costs only EVM gas, which is orders of magnitude cheaper than locking real BTC for a deposit. An attacker who wants to stall the withdrawal-polling loop needs only to submit many `withdrawalUTXOs` entries to the Citrea bridge contract before the target Bitcoin block is finalized. The `last_withdrawal_idx` cursor means the attacker must keep the flood sustained across blocks to prevent the verifier from ever catching up. The attack is amplified if the verifier is restarted or was briefly offline, because the entire accumulated backlog is then replayed in one pass.

---

### Recommendation

1. **Batch / paginate RPC calls**: Replace the one-at-a-time loop with a contract view that returns a slice (e.g., `depositTxIds(from, to)`) or use `eth_call` multicall to fetch multiple entries per round-trip.
2. **Cap per-block processing**: Introduce a configurable `max_entries_per_block` limit. If the backlog exceeds the cap, process the oldest N entries and defer the rest to the next block, ensuring `handle_finalized_block` always returns within a bounded time.
3. **Add an overall timeout**: Wrap `update_citrea_deposit_and_withdrawals` in a `tokio::time::timeout` so that a slow Citrea RPC cannot block the verifier indefinitely.
4. **Parallelize**: Use `futures::future::join_all` or a bounded semaphore to issue multiple RPC calls concurrently instead of sequentially.

---

### Proof of Concept

1. Deploy or interact with the Citrea bridge contract on a live network.
2. Register a large number of withdrawal UTXOs (e.g., 10 000) in a single Citrea block, paying only EVM gas per entry.
3. Wait for the corresponding Bitcoin block to be finalized.
4. Observe that `Verifier::handle_finalized_block` enters `collect_withdrawal_utxos` and issues 10 000 sequential RPC calls. With a 100 ms average RPC latency, this takes ~1 000 s — far exceeding a typical `operator_challenge_timeout_timelock` of a few hundred Bitcoin blocks (~hours, but the verifier is now behind by one block and cannot advance).
5. While the verifier is stalled, broadcast a fraudulent operator kickoff transaction. No challenge is submitted within the timeout window.
6. After the challenge window expires, the operator broadcasts the reimbursement transaction and claims the bridged BTC. [6](#0-5) [7](#0-6) [8](#0-7) [5](#0-4)

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
