### Title
Single Malfunctioning Kickoff/Round State Machine Permanently Halts All Bridge Automation — (`core/src/states/mod.rs`)

---

### Summary

`process_block_parallel` aggregates errors from every kickoff and round state machine into a single error check. If any one state machine captures a persistent error, the entire `NewFinalizedBlock` event fails, is never archived, and the `MessageConsumerTask` retries it until the `BufferedErrors` overflow limit (10) is reached — permanently stopping the task. This halts all bridge automation: watchtower challenges, operator asserts, reimbursements, and disprove transactions.

---

### Finding Description

**Root cause — `process_block_parallel` has no per-machine error isolation:** [1](#0-0) 

After running all kickoff and round state machine futures in parallel, every context's `errors` vector is drained into a single `all_errors` list. If any entry is non-empty the function returns `Err(...)` immediately, abandoning all other machines' results.

**Error capture path in state machine handlers:** [2](#0-1) 

State machine handlers call `capture_error` around every `dispatch_duty` call. Any `Err` from `handle_duty` (DB failure, RPC failure, transaction-building failure) is pushed into `ctx.errors`, which then surfaces in `process_block_parallel`.

**`handle_event` propagates the error without archiving the message:** [3](#0-2) 

`handle_event` calls `process_block_parallel` and propagates its error with `?`. The `MessageConsumerTask` only archives the message *after* a successful `handle_event`: [4](#0-3) 

A failing event is never archived; it stays at the head of the queue and is re-read on every subsequent `run_once` call.

**`BufferedErrors` permanently kills the task after 10 consecutive failures:** [5](#0-4) 

`recover_from_error` reloads state machines from the DB: [6](#0-5) 

If the error is caused by persistent on-chain data (e.g., a kickoff transaction whose witness encodes an invalid `payout_blockhash` that breaks transaction-building), reloading from DB does not help — the same data is re-processed and the same error recurs. After 10 attempts the task exits permanently.

**The `MessageConsumerTask` is configured with limit 10:** [7](#0-6) 

---

### Impact Explanation

Once the `MessageConsumerTask` stops:

- **Watchtower challenges are never sent** — malicious operators can claim reimbursement for invalid withdrawals unchallenged.
- **Operator asserts are never sent** — operators cannot defend themselves in the BitVM challenge game.
- **Disprove transactions are never sent** — verifiers cannot slash dishonest operators.
- **Reimbursement and round-advance transactions are never queued** — operator collateral is permanently locked.

All of these are automation duties dispatched through the state manager. A single stuck state machine blocks every other operator's and kickoff's processing on every subsequent Bitcoin block.

---

### Likelihood Explanation

Any operator participating in the bridge can broadcast a kickoff transaction. The `payout_blockhash` field is taken verbatim from the kickoff transaction's witness: [8](#0-7) 

If the witness encodes data that causes a persistent failure inside `handle_duty` (e.g., a transaction-building step that dereferences the blockhash as a Bitcoin block header and fails on malformed bytes), the resulting `capture_error` entry propagates through `process_block_parallel` and blocks every other state machine on every subsequent block. No privileged access is required beyond having a valid kickoff UTXO.

---

### Recommendation

Isolate per-machine errors in `process_block_parallel`. Instead of collecting all errors and returning on the first non-empty set, log and skip the offending machine (or quarantine it) and continue processing the remaining machines. This is the direct analog of the external report's fix: "bypass failed adapter calls."

```rust
// Instead of:
if !all_errors.is_empty() {
    return Err(...);
}

// Consider:
for (machine_id, err) in all_errors {
    tracing::error!("State machine {machine_id} error (skipping): {err:?}");
    // optionally quarantine the machine
}
```

Additionally, consider adding a per-machine error counter so that a repeatedly-failing machine is quarantined after N failures rather than blocking the entire pipeline.

---

### Proof of Concept

1. Operator broadcasts a kickoff transaction whose first-input witness encodes a `payout_blockhash` that triggers a persistent failure in `handle_duty` (e.g., malformed bytes that cause transaction-building to return `Err`).
2. `dispatch_new_kickoff_machine` enqueues a `SystemEvent::NewKickoff` message.
3. `MessageConsumerTask::run_once` dequeues the event; `handle_event` creates a `KickoffStateMachine` and adds it to the state manager.
4. On the next `NewFinalizedBlock` event, `process_block_parallel` runs all machines. The new kickoff machine calls `capture_error` around `dispatch_duty`, which fails persistently.
5. `process_block_parallel` returns `Err("Multiple errors occurred during state processing: ...")`.
6. `handle_event` propagates the error; the `NewFinalizedBlock` message is **not** archived.
7. `recover_from_error` reloads state from DB — the same broken kickoff machine is restored.
8. Steps 5–7 repeat. After 10 consecutive failures `BufferedErrors` returns `Err` from `run_once`, the `BackgroundTaskManager` marks `TaskVariant::StateManager` as `NotRunning`.
9. All subsequent Bitcoin blocks are never processed. Watchtower challenges, operator asserts, reimbursements, and disprove transactions are permanently halted across **all** operators and kickoffs. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** core/src/states/mod.rs (L592-615)
```rust
        while !kickoff_futures.is_empty() || !round_futures.is_empty() {
            // Execute all futures in parallel
            let (kickoff_results, round_results) =
                join(join_all(kickoff_futures), join_all(round_futures)).await;

            // Unzip the results into updated machines and state contexts
            let (mut changed_kickoff_machines, mut kickoff_contexts): (Vec<_>, Vec<_>) =
                kickoff_results.into_iter().unzip();
            let (mut changed_round_machines, mut round_contexts): (Vec<_>, Vec<_>) =
                round_results.into_iter().unzip();

            // Merge and handle errors
            let mut all_errors = Vec::new();
            for ctx in kickoff_contexts.iter_mut().chain(round_contexts.iter_mut()) {
                all_errors.extend(std::mem::take(&mut ctx.errors));
            }

            if !all_errors.is_empty() {
                // Return first error or create a combined error
                return Err(eyre::eyre!(
                    "Multiple errors occurred during state processing: {:?}",
                    all_errors
                ));
            }
```

**File:** core/src/states/context.rs (L217-225)
```rust
    pub async fn capture_error(
        &mut self,
        fnc: impl AsyncFnOnce(&mut Self) -> Result<(), eyre::Report>,
    ) {
        let result = fnc(self).await;
        if let Err(e) = result {
            self.errors.push(e.into());
        }
    }
```

**File:** core/src/states/event.rs (L112-122)
```rust
            SystemEvent::NewFinalizedBlock { block, height } => {
                if self.next_height_to_process != height {
                    return Err(eyre::eyre!("Finalized block arrived to state manager out of order. Expected: block at height {}, Got: block at height {}", self.next_height_to_process, height).into());
                }

                let mut context = self.new_context(dbtx.clone(), &block, height)?;

                self.process_block_parallel(&mut context).await?;

                self.last_finalized_block = Some(context.cache.clone());
            }
```

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

**File:** core/src/states/task.rs (L160-165)
```rust
impl<T: Owner + std::fmt::Debug + 'static> RecoverableTask for MessageConsumerTask<T> {
    async fn recover_from_error(&mut self, _error: &BridgeError) -> Result<(), BridgeError> {
        // in case of any error, reload the state machines from the database
        self.inner.reload_state_manager_from_db().await
    }
}
```

**File:** core/src/states/task.rs (L167-179)
```rust
impl<T: Owner + std::fmt::Debug + 'static> IntoTask for StateManager<T> {
    type Task = WithDelay<BufferedErrors<MessageConsumerTask<T>>>;

    /// Converts the StateManager into the consumer task with a polling delay.
    fn into_task(self) -> Self::Task {
        MessageConsumerTask {
            db: self.db.clone(),
            inner: self,
            queue_name: StateManager::<T>::queue_name(),
        }
        .into_buffered_errors(10, 3, Duration::from_secs(10))
        .with_delay(POLL_DELAY)
    }
```

**File:** core/src/task/mod.rs (L232-284)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let result = self.inner.run_once().await;

        match result {
            Ok(output) => {
                self.buffer.clear(); // clear buffer on first success
                Ok(output)
            }
            Err(e) => {
                tracing::error!(
                    "Task {:?} error, attempting to recover: {e:?}",
                    Self::VARIANT
                );
                // handle the error
                for attempt in 1..=self.handle_error_attempts {
                    let result = self.inner.recover_from_error(&e).await;
                    match result {
                        Ok(()) => break,
                        Err(e) => {
                            tracing::error!(
                                "Task {:?} error, failed to recover (attempt {attempt}): {e:?}",
                                Self::VARIANT,
                            );
                            if attempt == self.handle_error_attempts {
                                // this will only close the task thread
                                return Err(eyre::eyre!(
                                    "Failed to recover from task {:?} error after {attempt} attempts, aborting...",
                                    Self::VARIANT
                                ).into());
                            }
                            // wait for the configured duration (self.wait_between_recover_attempts) before trying again
                            tokio::time::sleep(self.wait_between_recover_attempts).await;
                        }
                    }
                }
                self.buffer.push(e);
                if self.buffer.len() >= self.error_overflow_limit {
                    let mut base_error: eyre::Report =
                        self.buffer.pop().expect("just inserted above").into();

                    for error in std::mem::take(&mut self.buffer) {
                        base_error = base_error.wrap_err(error);
                    }

                    base_error = base_error.wrap_err(format!(
                        "Exiting due to {} consecutive errors, the following chain is the list of errors.",
                        self.error_overflow_limit
                    ));

                    Err(base_error.into())
                } else {
                    Ok(Default::default())
                }
```

**File:** core/src/verifier.rs (L1516-1538)
```rust
                    let witness = tx
                        .input
                        .first()
                        .ok_or_else(|| eyre::eyre!("Kickoff transaction {txid} has no inputs"))?
                        .witness
                        .clone();
                    let (operator_xonly_pk, round_idx, kickoff_idx) =
                        kickoff_metadata
                            .get(&txid)
                            .ok_or_else(|| eyre::eyre!("Metadata not found for txid {}", txid))?;

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
```
