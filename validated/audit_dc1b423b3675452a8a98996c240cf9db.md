### Title
PGMQ State Manager Queue Has No Dead-Letter / Skip Mechanism — Conflicting `NewKickoff` Event Permanently Blocks Verifier Challenge Path — (File: `core/src/states/task.rs`, `core/src/states/event.rs`)

---

### Summary

The `MessageConsumerTask` reads `SystemEvent` messages from a PGMQ queue with a visibility timeout of zero and no dead-letter queue. When `handle_event` returns an error the message is never archived; it stays at the head of the FIFO queue and is retried on every subsequent poll. The `recover_from_error` hook only reloads in-memory state from the database — it does not skip or remove the failing message. After 10 consecutive failures `BufferedErrors` terminates the task. The `BackgroundTaskManager` marks it `NotRunning` but does **not** auto-restart it; even a manual `restart_background_tasks` RPC call recreates the task against the same unmodified queue, so the identical message is retried immediately and the task terminates again. The only escape is direct database surgery on the PGMQ table.

A concrete, code-acknowledged trigger exists: the `NewKickoff` conflict-detection branch in `handle_event` returns a hard error when the same `kickoff_data` is seen with different `payout_blockhash` or `kickoff_height`. The code comment explicitly names the scenario ("after a reorged kickoff was added too early"). Once the first `NewKickoff(K, H, P1)` is committed to the DB, any subsequent `NewKickoff(K, H', P2)` in the queue will fail on every retry, permanently blocking the verifier's state manager.

---

### Finding Description

**Queue consumer — no skip / dead-letter path**

`MessageConsumerTask::run_once` reads one message, calls `handle_event`, and only archives the message on success:

```
read_with_cxn(queue, vt=0, dbtx)   // VT=0: immediately re-visible on rollback
handle_event(message)?              // ? propagates error, archive never reached
archive_with_cxn(queue, msg_id)?    // only reached on Ok
dbtx.commit()
```

On any error the DB transaction rolls back, the message's `vt` resets to `now()` (VT=0), and the next poll reads the same message again. [1](#0-0) 

`recover_from_error` reloads state machines from the database but does not touch the queue:

```rust
async fn recover_from_error(&mut self, _error: &BridgeError) -> Result<(), BridgeError> {
    self.inner.reload_state_manager_from_db().await
}
``` [2](#0-1) 

`BufferedErrors` is configured with `error_overflow_limit = 10`; after 10 consecutive failures it returns `Err`, which terminates the `CancelableLoop` and marks the task `NotRunning`. [3](#0-2) 

**Conflicting `NewKickoff` — permanent error trigger**

`handle_event(NewKickoff)` iterates existing kickoff machines. If `kickoff_data` matches but any of `deposit_data`, `payout_blockhash`, or `kickoff_height` differs, it returns a hard error:

```rust
return Err(eyre::eyre!(
    "Conflicting kickoff({:?}) detected: same kickoff_data, mismatches: {}",
    kickoff_data, mismatches.join("; "),
).into());
``` [4](#0-3) 

The comment on the preceding guard explicitly acknowledges the reorg scenario:

```rust
// Same kickoff_data should always imply the same associated fields.
// This catches inconsistent finalized kickoff data, for example after a reorged kickoff was added too early.
``` [5](#0-4) 

**Two independent dispatch paths for the same kickoff**

`dispatch_new_kickoff_machine` is called from two independent code paths that run in separate tasks:

1. `Duty::CheckIfKickoff` handler in the verifier's `Owner::handle_duty` — triggered during `process_block_parallel` when the `RoundStateMachine` detects a kickoff UTXO spent. [6](#0-5) 

2. `handle_finalized_payout` inside the `LcpSyncerTask` — triggered when the LCP for the kickoff block is processed. [7](#0-6) 

If a reorg occurs between these two dispatches, path 1 may have enqueued `NewKickoff(K, H, P1)` (already committed to DB), while path 2 enqueues `NewKickoff(K, H', P2)` with the post-reorg block height and witness. The second event will fail on every retry.

**No auto-restart after task death**

`BackgroundTaskManager::ensure_task_looping` skips starting a task that is already `Running`, but once the task exits it is marked `NotRunning`. There is no watchdog that automatically calls `ensure_task_looping` again. [8](#0-7) 

The `restart_background_tasks` RPC creates a brand-new `StateManager` and calls `ensure_task_looping` again, but the PGMQ queue is shared and persistent — the conflicting message is still at the head, so the new task terminates within 10 polls. [9](#0-8) 

---

### Impact Explanation

Once the verifier's `MessageConsumerTask` is permanently blocked:

- No `NewFinalizedBlock` events are consumed → the verifier's state machines stop advancing.
- No `LCPProcessed` events are consumed → `check_if_kickoff_malicious` is never called.
- The verifier cannot issue a `WatchtowerChallenge` or `Disprove` transaction.
- An operator that submitted a fraudulent payout can wait out the challenge window unopposed and claim reimbursement from the bridge vault, stealing bridged BTC.

The operator's state manager queue is also affected by the same mechanism (the verifier dispatches `NewKickoff` to the operator's queue as well), blocking `ChallengeTimeout`, `AssertTimeout`, and reimbursement transaction submission. [10](#0-9) 

---

### Likelihood Explanation

The trigger requires a Bitcoin reorg that changes the witness data (`payout_blockhash`) of a kickoff transaction that was already finalized and committed to the verifier's DB. While deep reorgs are rare on mainnet, the finality depth is a configurable parameter and the code itself documents this exact failure mode. On testnet/signet the likelihood is higher. Additionally, any other permanent error inside `handle_event` (e.g., a DB constraint violation, integer overflow in block height conversion, or a future code change that adds a new hard-error path) would trigger the same queue-blocking outcome with no recovery path.

---

### Recommendation

1. **Add a dead-letter queue or skip mechanism**: When `handle_event` fails after a configurable number of retries (e.g., `read_ct` exceeds a threshold), archive the message to a dead-letter PGMQ table instead of leaving it at the head of the queue. PGMQ's `archive` function can be used for this.

2. **Fix the conflicting `NewKickoff` handler**: Instead of returning a hard error on conflict, either (a) update the existing kickoff machine with the new data if the old one has not yet been acted upon, or (b) treat the conflict as a warning and skip the duplicate, since the first committed machine is already being tracked.

3. **Increase visibility timeout**: Set VT > 0 (e.g., 60 seconds) so that a failing message is not immediately re-queued, giving other messages a chance to be processed and providing a natural back-off.

4. **Add a watchdog**: The `BackgroundTaskManager` should detect `NotRunning` tasks and attempt automatic restart, or at minimum emit a metric/alert that triggers operator intervention.

---

### Proof of Concept

**Setup**: Verifier and operator running with automation. A deposit has been finalized and a kickoff transaction `K` is on-chain at block `H` with witness `P1`.

**Step 1**: `BlockFetcherTask` delivers block `H` to the state manager queue as `NewFinalizedBlock(H)`.

**Step 2**: `MessageConsumerTask` processes `NewFinalizedBlock(H)`. Inside `process_block_parallel`, the `RoundStateMachine` detects the kickoff UTXO spent and dispatches `Duty::CheckIfKickoff`. The verifier's `handle_duty` calls `dispatch_new_kickoff_machine(K, H, P1)`, enqueuing `NewKickoff(K, H, P1)`. The block event is committed and archived.

**Step 3**: `MessageConsumerTask` processes `NewKickoff(K, H, P1)`. A `KickoffStateMachine` is created, saved to DB, and the message is archived.

**Step 4**: A shallow Bitcoin reorg replaces block `H` with block `H'`. The kickoff transaction is re-included with a different witness `P2` (e.g., different `payout_blockhash` encoding). The `LcpSyncerTask` processes the finalized kickoff at `H'` and calls `dispatch_new_kickoff_machine(K, H', P2)`, enqueuing `NewKickoff(K, H', P2)`.

**Step 5**: `MessageConsumerTask` reads `NewKickoff(K, H', P2)`. `handle_event` iterates `kickoff_machines`, finds the existing machine with `kickoff_data=K` (loaded from DB), detects `payout_blockhash` mismatch (`P1` vs `P2`) and `kickoff_height` mismatch (`H` vs `H'`), and returns:
```
Err("Conflicting kickoff(K) detected: same kickoff_data, mismatches: payout_blockhash_witness: ...; kickoff_height: H vs H'")
```
The DB transaction rolls back. The message is not archived. VT=0 makes it immediately visible.

**Step 6**: `recover_from_error` calls `reload_state_manager_from_db`, which loads the committed machine `(K, H, P1)` from DB. The in-memory state is identical to before. The next poll reads the same `NewKickoff(K, H', P2)` message and fails again.

**Step 7**: After 10 consecutive failures, `BufferedErrors` terminates the task. The verifier's state manager is dead. All subsequent `NewFinalizedBlock`, `NewKickoff`, and `LCPProcessed` events accumulate in the queue unprocessed. The verifier cannot detect malicious kickoffs or issue challenges. An operator submitting a fraudulent payout can claim reimbursement from the bridge vault after the challenge window expires.

### Citations

**File:** core/src/states/task.rs (L115-156)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let new_event_received = async {
            let mut dbtx = self.db.begin_transaction().await?;

            // Poll new event
            let Some(Message {
                msg_id, message, ..
            }): Option<Message<SystemEvent>> = self
                .inner
                .queue
                // 2nd param of read_with_cxn is the visibility timeout, set to 0 as we only have 1 consumer of the queue, which is the state machine
                // visibility timeout is the time after which the message is visible again to other consumers
                .read_with_cxn(&self.queue_name, 0, &mut *dbtx)
                .await
                .wrap_err("Reading event from queue")?
            else {
                dbtx.commit().await?;
                return Ok::<_, BridgeError>(false);
            };

            let arc_dbtx = Arc::new(Mutex::new(dbtx));

            self.inner.handle_event(message, arc_dbtx.clone()).await?;

            let mut dbtx = Arc::into_inner(arc_dbtx)
                .ok_or_eyre("Expected single reference to DB tx when committing")?
                .into_inner();

            // Delete event from queue
            self.inner
                .queue
                .archive_with_cxn(&self.queue_name, msg_id, &mut *dbtx)
                .await
                .wrap_err("Deleting event from queue")?;

            dbtx.commit().await?;
            Ok(true)
        }
        .await?;

        Ok(new_event_received)
    }
```

**File:** core/src/states/task.rs (L160-164)
```rust
impl<T: Owner + std::fmt::Debug + 'static> RecoverableTask for MessageConsumerTask<T> {
    async fn recover_from_error(&mut self, _error: &BridgeError) -> Result<(), BridgeError> {
        // in case of any error, reload the state machines from the database
        self.inner.reload_state_manager_from_db().await
    }
```

**File:** core/src/states/task.rs (L171-178)
```rust
    fn into_task(self) -> Self::Task {
        MessageConsumerTask {
            db: self.db.clone(),
            inner: self,
            queue_name: StateManager::<T>::queue_name(),
        }
        .into_buffered_errors(10, 3, Duration::from_secs(10))
        .with_delay(POLL_DELAY)
```

**File:** core/src/states/event.rs (L189-193)
```rust
                    // Same kickoff_data should always imply the same associated fields.
                    // This catches inconsistent finalized kickoff data, for example after a reorged kickoff was added too early.
                    if deposit_data_matches && payout_blockhash_matches && kickoff_height_matches {
                        return Ok(());
                    }
```

**File:** core/src/states/event.rs (L218-223)
```rust
                    return Err(eyre::eyre!(
                        "Conflicting kickoff({:?}) detected: same kickoff_data, mismatches: {}",
                        kickoff_data,
                        mismatches.join("; "),
                    )
                    .into());
```

**File:** core/src/verifier.rs (L1527-1539)
```rust
                    StateManager::<Self>::dispatch_new_kickoff_machine(
                        &self.db,
                        dbtx,
                        KickoffData {
                            operator_xonly_pk: *operator_xonly_pk,
                            round_idx: *round_idx,
                            kickoff_idx: *kickoff_idx as u32,
                        },
                        block_height,
                        deposit_data.clone(),
                        witness.clone(),
                    )
                    .await?;
```

**File:** core/src/verifier.rs (L1541-1561)
```rust
                    // send it to operator state manager too, if the state manager queue for the operator exists
                    // if it doesn't exist, it means this verifier does not have an operator with automation enabled
                    if self
                        .db
                        .pgmq_queue_exists(&StateManager::<Operator<C>>::queue_name(), Some(dbtx))
                        .await?
                    {
                        StateManager::<Operator<C>>::dispatch_new_kickoff_machine(
                            &self.db,
                            dbtx,
                            KickoffData {
                                operator_xonly_pk: *operator_xonly_pk,
                                round_idx: *round_idx,
                                kickoff_idx: *kickoff_idx as u32,
                            },
                            block_height,
                            deposit_data.clone(),
                            witness.clone(),
                        )
                        .await?;
                    }
```

**File:** core/src/verifier.rs (L3351-3359)
```rust
                        StateManager::<Self>::dispatch_new_kickoff_machine(
                            &self.db,
                            dbtx,
                            kickoff_data,
                            block_height,
                            deposit_data.clone(),
                            witness.clone(),
                        )
                        .await?;
```

**File:** core/src/task/manager.rs (L144-171)
```rust
    pub async fn ensure_task_looping<S, U: IntoTask<Task = S>>(&self, task: U)
    where
        S: Task + Sized + std::fmt::Debug,
        <S as Task>::Output: Into<bool>,
    {
        self.ensure_monitor_running().await;

        let variant = S::VARIANT;

        // do not start the same task if it is already running
        if self.is_task_running(variant).await {
            tracing::debug!("Task {:?} is already running, skipping", variant);
            return;
        }

        let task = task.into_task();
        let (task, cancel_tx) = task.cancelable_loop();

        let join_handle = task.into_bg();
        let abort_handle = join_handle.abort_handle();

        self.task_registry.write().await.insert(
            variant,
            (TaskStatus::Running, abort_handle, Some(cancel_tx)),
        );

        self.monitor_spawned_task(join_handle, variant);
    }
```

**File:** core/src/rpc/operator.rs (L60-73)
```rust
    async fn restart_background_tasks(
        &self,
        _request: tonic::Request<super::Empty>,
    ) -> std::result::Result<tonic::Response<super::Empty>, tonic::Status> {
        tracing::info!("Restarting background tasks rpc called");
        timed_request(
            RESTART_BACKGROUND_TASKS_TIMEOUT,
            "Restarting background tasks",
            self.start_background_tasks(),
        )
        .await?;
        tracing::info!("Restarting background tasks rpc completed");
        Ok(Response::new(Empty {}))
    }
```
