## Title
Program cache cooperative-loading task can permanently stall all validators if the loading thread panics before `finish_cooperative_loading_task` - ([File: program-runtime/src/loaded_programs.rs], [File: svm/src/transaction_processor.rs])

### Summary
The `ArtGobblers` bug is a state machine that sets a "waiting" flag (`waitingForSeed`) which can only be cleared by a single external actor (`randProvider`), and no other actor can clear it, so if that actor fails to respond, the flag is stuck forever and blocks all subsequent legitimate operations. Agave's `ProgramCache` cooperative-loading mechanism has the same structural shape: one thread claims exclusive responsibility for loading a program (`loading_entries` map) and is the *only* one that can clear that claim (via `finish_cooperative_loading_task`, which calls `LoadingTaskWaiter::notify()`). If that thread dies (panics) before calling `finish_cooperative_loading_task`, every other thread that needs the same program is parked forever in `LoadingTaskWaiter::wait()` with no other path to recovery.

### Finding Description
In `ProgramCache::extract`, when a program is missing from the cache and no cooperative loading task has yet been claimed for the batch, the current thread inserts an entry into the shared `loading_entries` map and becomes solely responsible for loading it: [1](#0-0) 

This entry is only ever removed by that same thread calling `finish_cooperative_loading_task`, which also fires `self.loading_task_waiter.notify()` to wake up all other threads waiting on this program: [2](#0-1) 

The caller of this flow, `TransactionBatchProcessor::replenish_program_cache`, drives the load with `load_program_with_pubkey(...).expect("called load_program_with_pubkey() with nonexistent account")`, and any other concurrent thread that could not claim the loading task instead blocks on `task_waiter.wait(task_cookie)`: [3](#0-2) 

`LoadingTaskWaiter::wait` is a plain condvar wait with no timeout and no alternate wake-up path: [4](#0-3) 

If the thread that owns the cooperative-loading claim for a given `program_id` panics (e.g. via the `.expect(...)` above, or any other panic inside `load_program_with_pubkey`) before it reaches `finish_cooperative_loading_task`, the `loading_entries[program_id]` entry is never removed and `notify()` is never called. Just like `ArtGobblers.gobblerRevealsData.waitingForSeed`, which can only be cleared by `randProvider`, this per-program "being loaded" claim can only be cleared by the one thread that took it — and if that thread is gone, the claim is permanently stuck.

### Impact Explanation
Once a program's `loading_entries` claim is orphaned:
- `extract()` will never insert a fresh cooperative-loading task for that `program_id` again, because the map slot is occupied forever (`Entry::Vacant` branch never triggers for that key).
- Every other worker thread across every subsequent transaction batch that references that program will fall into the `else` branch of `replenish_program_cache` and block on `task_waiter.wait(...)` indefinitely, since no thread will ever call `notify()` for that key again.
- Because this is the *global* program cache shared across the whole validator (and used identically by every validator node running the same code), any transaction touching that program hangs the processing thread permanently, effectively halting all execution paths that depend on that program (including replay/block-production threads), producing a validator-wide liveness failure analogous to the "reveal process bricked forever" impact in the original finding.

This is a genuine node-hang/DoS in the core BPF program cache path, not a peer/validator-role-only issue — it is triggered purely by unprivileged, ordinary transaction traffic causing a program to be loaded.

### Likelihood Explanation
The trigger condition is a panic inside the single-threaded load path (`load_program_with_pubkey` or the hard `.expect()` guarding it) occurring after the `loading_entries` claim is taken but before `finish_cooperative_loading_task` runs. The code currently treats "account existed when `missing_programs` was computed" and "account still exists when actually loaded" as an invariant enforced only by an `.expect()`, i.e., a hard panic rather than a recoverable error, with no cleanup-on-panic (no `Drop` guard, no poisoning-aware removal) for the `loading_entries` claim. Any code path that violates that invariant — including future refactors, unexpected bank/AccountLoader inconsistencies, or transient allocator/OOM panics during ELF verification/compilation inside `load_program_with_pubkey` — turns into a permanent liveness bug with no operator remediation short of a full validator restart (which clears the in-memory `ProgramCache`).

### Recommendation
- Never allow a panic to leave the cooperative-loading claim unrepaired: wrap the loading step in a `Drop`-based guard that removes the `loading_entries[program_id]` entry and calls `loading_task_waiter.notify()` even on unwind, so a panicked loader thread cannot permanently strand other threads.
- Replace the `.expect("called load_program_with_pubkey() with nonexistent account")` panic with a proper `Result`/tombstone path (mirroring other tombstone handling already present in this module), so a genuinely missing account degrades to a `FailedVerification`/`Closed` cache entry instead of aborting the thread.
- Consider bounding `LoadingTaskWaiter::wait` with a timeout so that even an unforeseen stuck claim self-heals by allowing a waiting thread to re-attempt claiming the loading task after some interval.

### Proof of Concept
Conceptually mirrors the original ArtGobblers PoC:
1. Thread A processes a batch requiring `program_id = P`, which is not yet in `ProgramCache`. `extract()` inserts `loading_entries[P] = (slot, thread_A)` and returns `Some(P)` as its cooperative loading task (`program-runtime/src/loaded_programs.rs:735-746`).
2. Thread A calls `load_program_with_pubkey(...).expect(...)` in `svm/src/transaction_processor.rs:930-937`; suppose this call panics (e.g., due to an unexpected missing/invalid account, or any other panic surfaced through ELF verification/compilation).
3. Thread A's stack unwinds without ever reaching `global_program_cache.finish_cooperative_loading_task(...)` (`svm/src/transaction_processor.rs:945-959`), so `loading_entries[P]` is never removed and `loading_task_waiter.notify()` is never called.
4. Any other thread (Thread B, C, ...) processing a batch that also references `P` calls `extract()`, sees `loading_entries[P]` already occupied, gets `cooperative_loading_task = None`, and falls into `task_waiter.wait(task_cookie)` in `svm/src/transaction_processor.rs:963-969`.
5. Since no thread will ever call `notify()` for `P` again, all current and future threads that need `P` block forever — a permanent, validator-wide hang for any transaction touching that program.

### Citations

**File:** program-runtime/src/loaded_programs.rs (L735-746)
```rust
                    if cooperative_loading_task.is_none() {
                        let mut loading_entries = loading_entries.lock().unwrap();
                        let entry = loading_entries.entry(*program_to_load.program_id);
                        if let Entry::Vacant(entry) = entry {
                            entry.insert((
                                loaded_programs_for_tx_batch.slot,
                                thread::current().id(),
                            ));
                            cooperative_loading_task = Some(*program_to_load.program_id);
                        }
                    }
                    true
```

**File:** program-runtime/src/loaded_programs.rs (L764-804)
```rust
    pub fn finish_cooperative_loading_task(
        &mut self,
        program_runtime_environment: &ProgramRuntimeEnvironment,
        current_slot: Slot,
        key: Pubkey,
        last_modification_slot: Slot,
        loaded_program: Arc<ProgramCacheEntry>,
    ) -> bool {
        match &mut self.index {
            IndexImplementation::V1 {
                loading_entries, ..
            } => {
                let loading_thread = loading_entries.get_mut().unwrap().remove(&key);
                debug_assert_eq!(loading_thread, Some((current_slot, thread::current().id())));
                // Check that it will be visible to our own fork once inserted
                if loaded_program.deployment_slot > self.latest_root_slot
                    && !matches!(
                        self.fork_graph
                            .as_ref()
                            .unwrap()
                            .upgrade()
                            .unwrap()
                            .read()
                            .unwrap()
                            .relationship(loaded_program.deployment_slot, current_slot),
                        BlockRelation::Equal | BlockRelation::Ancestor
                    )
                {
                    self.stats.lost_insertions.fetch_add(1, Ordering::Relaxed);
                }
                let was_occupied = self.assign_program(
                    program_runtime_environment,
                    key,
                    last_modification_slot,
                    loaded_program,
                );
                self.loading_task_waiter.notify();
                was_occupied
            }
        }
    }
```

**File:** svm/src/transaction_processor.rs (L928-971)
```rust
            let program_to_store = program_to_load.map(|key| {
                // Load, verify and compile one program.
                let (program, last_modification_slot) = load_program_with_pubkey(
                    account_loader,
                    program_runtime_environment_for_execution,
                    &key,
                    self.slot,
                    execute_timings,
                )
                .expect("called load_program_with_pubkey() with nonexistent account");
                (key, program, last_modification_slot)
            });

            if let Some((key, program, last_modification_slot)) = program_to_store {
                program_cache_for_tx_batch.loaded_missing = true;
                let mut global_program_cache = self.global_program_cache.write().unwrap();
                // Submit our last completed loading task.
                if global_program_cache.finish_cooperative_loading_task(
                    program_runtime_environment_for_execution,
                    self.slot,
                    key,
                    last_modification_slot,
                    program,
                ) && limit_to_load_programs
                {
                    // This branch is taken when there is an error in assigning a program to a
                    // cache slot. It is not possible to mock this error for SVM unit
                    // tests purposes.
                    *program_cache_for_tx_batch = ProgramCacheForTxBatch::new(self.slot);
                    program_cache_for_tx_batch.hit_max_limit = true;
                    return;
                }
            } else if missing_programs.is_empty() {
                break;
            } else {
                // Remember: there are multiple transaction processor threads running concurrently
                // and those other threads may be loading this or other programs.
                //
                // So, sleep until some other thread submits a program with their
                // `finish_cooperative_loading_task` call. We'll then wake up and try to load the
                // missing programs inside the tx batch again.
                let _new_cookie = task_waiter.wait(task_cookie);
            }
        }
```

**File:** program-runtime/src/loading_task.rs (L42-48)
```rust
    pub fn wait(&self, cookie: LoadingTaskCookie) -> LoadingTaskCookie {
        let cookie_guard = self.cookie.lock().unwrap();
        *self
            .cond
            .wait_while(cookie_guard, |current_cookie| *current_cookie == cookie)
            .unwrap()
    }
```
