### Title
Citrea L2 Downtime Spanning the Kickoff Challenge Window Prevents Malicious Kickoff Detection, Enabling Operator Deposit Theft — (`core/src/verifier.rs`)

---

### Summary

`Verifier::handle_finalized_block` gates all per-block processing — including the `dispatch_lcp_processed` call that triggers `check_if_kickoff_malicious` — behind a blocking call to `get_citrea_l2_height_range`. If the Citrea light-client prover is unavailable for the duration of the `operator_challenge_timeout_timelock` (144 Bitcoin blocks ≈ 24 hours), a malicious operator can send a kickoff transaction without having fronted a real payout, wait out the challenge window unchallenged, broadcast the pre-signed `ChallengeTimeout` transaction, and claim the full deposit reimbursement.

---

### Finding Description

**Step 1 — Citrea availability gates the entire block handler.**

`Verifier::handle_finalized_block` is the sole implementation of `BlockHandler::handle_new_block` for the `LcpSyncerTask`. Its very first action is a blocking poll:

```rust
let (l2_height_start, l2_height_end) = self
    .citrea_client
    .get_citrea_l2_height_range(block_height.into(), timeout, ...)
    .await
    .inspect_err(...)?;   // propagates error → rolls back dbtx
``` [1](#0-0) 

`get_citrea_l2_height_range` polls `get_light_client_proof_by_l1_height` in a loop and returns an error after `timeout` (default `TEN_MINUTES_IN_SECS`) if no proof is available:

```rust
if start.elapsed() > timeout {
    return Err(eyre::eyre!(
        "Light client proof not found for block height {} after {} seconds", ...
    ).into());
}
``` [2](#0-1) 

**Step 2 — On failure, `dispatch_lcp_processed` is never called.**

If the call above errors, the `?` operator propagates the error, the database transaction is rolled back, and the code below is never reached:

```rust
#[cfg(feature = "automation")]
{
    ...
    StateManager::<Self>::dispatch_lcp_processed(&self.db, dbtx, block_height).await?;
}
``` [3](#0-2) 

**Step 3 — `check_if_kickoff_malicious` is only triggered by `LCPProcessed`.**

The state manager's `handle_event` only calls `check_if_kickoff_malicious` in two places: when a `NewKickoff` event arrives and `last_processed_lcp >= kickoff_height`, or when an `LCPProcessed` event arrives:

```rust
SystemEvent::LCPProcessed { height } => {
    let kickoffs_to_check: Vec<_> = self
        .kickoff_machines
        .iter()
        .filter(|machine| machine.kickoff_height == height)
        ...
    for (payout_blockhash, kickoff_data, deposit_data) in kickoffs_to_check {
        self.check_if_kickoff_malicious(...).await?;
    }
    self.last_processed_lcp = Some(height);
}
``` [4](#0-3) 

If `LCPProcessed` is never dispatched for the kickoff's Bitcoin height, `check_if_kickoff_malicious` is never called, and no challenge transaction is queued.

**Step 4 — The kickoff detection path is independent of Citrea.**

The `BlockFetcherTask` (state manager's block consumer) sends `NewFinalizedBlock` events to the queue without touching Citrea, so kickoff state machines are created normally. However, the malicious-kickoff check is deferred until `LCPProcessed` arrives. [5](#0-4) 

**Step 5 — `ChallengeTimeout` expires before Citrea recovers.**

The `ChallengeTimeout` transaction is pre-signed and spendable after `operator_challenge_timeout_timelock` (144 blocks, ≈ 24 hours):

```rust
Sequence::from_height(paramset.operator_challenge_timeout_timelock),
``` [6](#0-5) 

Once broadcast, it spends the `KickoffFinalizer` UTXO, transitioning the kickoff state machine to `Closed`. When Citrea eventually recovers and `LCPProcessed` is dispatched, the kickoff machine is already closed and the challenge UTXO is already spent — no challenge can be sent retroactively.

**Step 6 — `is_kickoff_malicious` would have caught the fraud, but is never invoked.**

```rust
let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
    tracing::warn!(
        "No payout info found in db for move txid {move_txid}, assuming malicious"
    );
    return Ok(true);
};
``` [7](#0-6) 

If the operator never fronted a real payout, `get_payout_info_from_move_txid` returns `None` and the function correctly returns `true` (malicious). But this function is never reached because `check_if_kickoff_malicious` is never triggered.

**Step 7 — `FinalizedBlockFetcherTask::recover_from_error` does nothing.**

The `LcpSyncerTask` wraps `FinalizedBlockFetcherTask`, whose error recovery is a no-op:

```rust
async fn recover_from_error(&mut self, _error: &BridgeError) -> Result<(), BridgeError> {
    // No action needed. Errors will cause a rollback and the task will retry on the next run.
    Ok(())
}
``` [8](#0-7) 

`next_finalized_height` is only advanced after a successful commit, so the task retries the same block indefinitely while Citrea is down.

---

### Impact Explanation

A malicious operator can steal the full deposit (`bridge_amount`, configured at 1,000,000,000 satoshis = 10 BTC) by:

1. Sending a kickoff transaction without having fronted a legitimate payout.
2. Exploiting a Citrea light-client prover outage lasting ≥ 144 Bitcoin blocks (≈ 24 hours) that coincides with the kickoff.
3. Broadcasting the pre-signed `ChallengeTimeout` transaction after the timelock expires.
4. Proceeding through `ReadyToReimburse` → `Reimburse` to claim the deposit UTXO.

The operator's collateral (`operator_challenge_amount` = 200,000,000 sats = 2 BTC) is not slashed because no challenge was sent. Net theft: 10 BTC − 2 BTC collateral at risk = 8 BTC profit per deposit.

---

### Likelihood Explanation

- Citrea is a new L2 rollup; the light-client prover is a complex ZK-proving service that can experience outages.
- The required downtime window (144 blocks ≈ 24 hours) is long but not implausible for a new system.
- An adversarial operator could time the attack to coincide with a known maintenance window or a network incident.
- The operator must already be a registered participant with deposited collateral, limiting the attacker pool but not eliminating the risk.

---

### Recommendation

Decouple the `dispatch_lcp_processed` signal from the Citrea availability check. Specifically:

1. **Split `handle_finalized_block` into two phases**: (a) Bitcoin-only processing (`update_finalized_payouts`, kickoff detection, `dispatch_lcp_processed`) which runs unconditionally, and (b) Citrea sync (`update_citrea_deposit_and_withdrawals`) which can fail and retry independently.

2. **Dispatch `LCPProcessed` based on Bitcoin block height, not Citrea proof availability**: The kickoff malicious check only needs the payout info (from Bitcoin) and the Citrea withdrawal registration. The `LCPProcessed` signal should be sent as soon as the Bitcoin block is processed, with the Citrea data fetched lazily or retried separately.

3. **Add a deadline-based fallback**: If `LCPProcessed` for a kickoff height has not been dispatched within `operator_challenge_timeout_timelock / 2` blocks, automatically queue the challenge transaction as a precautionary measure (similar to the "challenge period" recommendation in M-11).

---

### Proof of Concept

```
Bitcoin height H:   Operator sends kickoff tx (no real payout fronted)
                    LcpSyncerTask tries to process block H
                    → get_citrea_l2_height_range(H) times out (Citrea down)
                    → handle_finalized_block returns Err
                    → dbtx rolled back, next_finalized_height stays at H-1
                    → dispatch_lcp_processed(H) never called
                    → LCPProcessed(H) never sent to state manager
                    → check_if_kickoff_malicious never triggered for kickoff at H

Bitcoin height H+144: operator_challenge_timeout_timelock expires
                    Operator broadcasts pre-signed ChallengeTimeout tx
                    → KickoffFinalizer UTXO spent
                    → Kickoff state machine transitions to Closed
                    → Challenge UTXO no longer spendable

Bitcoin height H+144+N: Citrea recovers
                    LcpSyncerTask processes block H
                    → dispatch_lcp_processed(H) called
                    → LCPProcessed(H) sent to state manager
                    → kickoff_machines.filter(height == H) → machine is Closed
                    → check_if_kickoff_malicious called but challenge UTXO already spent
                    → No challenge can be sent

Operator proceeds:  ReadyToReimburse → Reimburse
                    → Spends MoveToVault UTXO (the deposit)
                    → Operator receives bridge_amount BTC
```

### Citations

**File:** core/src/verifier.rs (L1875-1880)
```rust
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L3067-3075)
```rust
        let (l2_height_start, l2_height_end) = self
            .citrea_client
            .get_citrea_l2_height_range(
                block_height.into(),
                timeout,
                self.config.protocol_paramset(),
            )
            .await
            .inspect_err(|e| tracing::error!("Error getting citrea l2 height range: {:?}", e))?;
```

**File:** core/src/verifier.rs (L3093-3105)
```rust
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
```

**File:** core/src/citrea.rs (L567-574)
```rust
            if start.elapsed() > timeout {
                return Err(eyre::eyre!(
                    "Light client proof not found for block height {} after {} seconds",
                    block_height,
                    timeout.as_secs()
                )
                .into());
            }
```

**File:** core/src/states/event.rs (L276-313)
```rust
            SystemEvent::LCPProcessed { height } => {
                let kickoffs_to_check: Vec<_> = self
                    .kickoff_machines
                    .iter()
                    .filter(|machine| machine.kickoff_height == height)
                    .map(|machine| {
                        (
                            machine.payout_blockhash.clone(),
                            machine.kickoff_data,
                            machine.deposit_data.clone(),
                        )
                    })
                    .collect();

                if !kickoffs_to_check.is_empty() {
                    // create a dummy context for duty processing, a block is not needed for LCPProcessed
                    let mut dummy_context = self.new_context_with_block_cache(
                        dbtx.clone(),
                        self.last_finalized_block.clone().ok_or_eyre(
                            "Last finalized block not found, should always be Some after initialization",
                        )?,
                    )?;

                    for (payout_blockhash, kickoff_data, deposit_data) in kickoffs_to_check {
                        self.check_if_kickoff_malicious(
                            &payout_blockhash,
                            &kickoff_data,
                            &deposit_data,
                            &mut dummy_context,
                        )
                        .await?;
                    }
                }

                tracing::info!("LCP processed for height: {}", height);

                self.last_processed_lcp = Some(height);
            }
```

**File:** core/src/states/task.rs (L34-52)
```rust
#[async_trait]
impl BlockHandler for QueueBlockHandler {
    /// Handles a new block by sending a new block event to the queue.
    /// State manager will process the block after reading the event from the queue.
    async fn handle_new_block(
        &mut self,
        dbtx: DatabaseTransaction<'_>,
        _block_id: u32,
        block: bitcoin::Block,
        height: u32,
    ) -> Result<(), BridgeError> {
        let event = SystemEvent::NewFinalizedBlock { block, height };

        self.queue
            .send_with_cxn(&self.queue_name, &event, &mut **dbtx)
            .await
            .wrap_err("Error sending new block event to queue")?;
        Ok(())
    }
```

**File:** core/src/builder/transaction/challenge.rs (L377-377)
```rust
            Sequence::from_height(paramset.operator_challenge_timeout_timelock),
```

**File:** core/src/bitcoin_syncer.rs (L590-595)
```rust
impl<H: BlockHandler> RecoverableTask for FinalizedBlockFetcherTask<H> {
    async fn recover_from_error(&mut self, _error: &BridgeError) -> Result<(), BridgeError> {
        // No action needed. Errors will cause a rollback and the task will retry on the next run.
        // In-memory data remains in sync (as it's only updated after db commit is successful).
        Ok(())
    }
```
