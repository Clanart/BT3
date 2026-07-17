### Title
Silent Failure in State Sync Dump: Failed Shard Upload Marked as `AllDumped` — (`File: nearcore/src/state_sync.rs`)

### Summary

In `StateDumper::check_parts_upload`, when a shard's state-part upload task finishes with an error, the error is logged but execution unconditionally proceeds to write `StateSyncDumpProgress::AllDumped` to the persistent store for that shard. On the next node restart, `check_old_progress` reads this `AllDumped` marker and permanently skips re-dumping the shard. The shard's state parts are never actually uploaded to external storage, yet every subsequent epoch cycle treats the dump as complete. Any node that later attempts state sync from external storage for that shard/epoch will find no parts and fail to reconstruct the shard state.

### Finding Description

`check_parts_upload` awaits the result of `dump.await_parts_upload()`, which returns `(ShardId, anyhow::Result<()>)`. The `match` block on `result` logs the error path but does **not** gate the subsequent `set_state_sync_dump_progress` call:

```rust
// nearcore/src/state_sync.rs  lines 841-866
async fn check_parts_upload(&mut self) {
    ...
    let (shard_id, result) = dump.await_parts_upload().await;

    match result {
        Ok(()) => { tracing::info!(..., "shard dump finished"); }
        Err(error) => {
            tracing::error!(..., "shard dump failed");   // ← error consumed here
        }
    }

    // ← unconditional: runs even when result was Err
    self.chain.chain_store().set_state_sync_dump_progress(
        shard_id,
        Some(StateSyncDumpProgress::AllDumped {
            epoch_id: dump.epoch_id,
            epoch_height: dump.epoch_height,
        }),
    );
    ...
}
``` [1](#0-0) 

The `AllDumped` variant is the terminal "done" state: [2](#0-1) 

On the next startup, `check_old_progress` iterates stored progress records and, for any shard whose stored entry is `done == true` (which `AllDumped` satisfies), removes it from `dump.dump_state` and `senders`, permanently skipping the re-dump: [3](#0-2) 

The error path in `dump_shard_state` is reachable: if any `upload_state_part` future spawned via `respawn_for_parallelism` panics (e.g., due to a runtime bug or OOM), the `sender` receives `Err`, which propagates through `await_parts_upload` to `check_parts_upload`: [4](#0-3) 

Additionally, the existing code comment explicitly acknowledges that `upload_state_part` is intended to return non-retryable errors in the future ("this should be changed to return Err() if the error is not going to be retryable"), meaning the silent-failure window will widen as that TODO is resolved: [5](#0-4) 

### Impact Explanation

The corrupted persistent value is `StateSyncDumpProgress::AllDumped { epoch_id, epoch_height }` written to `DBCol::BlockMisc` under key `STATE_SYNC_DUMP:<ShardId>` for a shard whose parts were never actually uploaded. Any node that relies on external storage (S3/GCS) for state sync will request parts for that `(epoch_id, shard_id)` pair, receive 404s for every part, and be unable to complete state sync. This blocks new nodes from joining the network and prevents lagging nodes from catching up for the affected epoch.

### Likelihood Explanation

Currently `upload_state_part` retries upload errors indefinitely, so the `Err` path in `dump_shard_state` is reached only on task panics (e.g., runtime bug, OOM, or stack overflow in the spawned future). This makes the bug latent but not theoretical. The likelihood increases materially once the acknowledged TODO is resolved to return non-retryable errors, at which point any transient external-storage failure during a dump would permanently suppress re-dumping for that shard.

### Recommendation

Gate the `set_state_sync_dump_progress` call on a successful result. Only write `AllDumped` when the upload actually succeeded:

```rust
match result {
    Ok(()) => {
        tracing::info!(..., "shard dump finished");
        self.chain.chain_store().set_state_sync_dump_progress(
            shard_id,
            Some(StateSyncDumpProgress::AllDumped {
                epoch_id: dump.epoch_id,
                epoch_height: dump.epoch_height,
            }),
        );
    }
    Err(error) => {
        tracing::error!(..., "shard dump failed");
        // Do NOT write AllDumped; leave progress as InProgress so the
        // next epoch cycle or restart will retry the dump.
    }
}
```

### Proof of Concept

1. A node is configured as a state sync dumper for shard S in epoch E.
2. During `check_parts_upload`, `dump.await_parts_upload()` returns `Err` (e.g., a spawned `upload_state_part` task panics due to a runtime bug).
3. `check_parts_upload` logs the error and then unconditionally writes `StateSyncDumpProgress::AllDumped { epoch_id: E, epoch_height: H }` to `DBCol::BlockMisc` for shard S.
4. The node restarts. `check_old_progress` reads the stored entry, sees `done == true`, removes shard S from `dump.dump_state`, and skips re-dumping it.
5. External storage contains zero state parts for shard S / epoch E.
6. A new node attempts state sync for epoch E. It downloads the state header (which was uploaded before the failure), computes `num_parts`, and requests all parts from external storage. Every request returns 404. State sync for shard S never completes. The node cannot join the network. [6](#0-5) [7](#0-6)

### Citations

**File:** nearcore/src/state_sync.rs (L338-342)
```rust
    /// Attempt to generate the state part for `self.epoch_id`, `self.shard_id` and `part_idx`, and upload it to
    /// the external storage. The state part generation is limited by the number of permits allocated to the `obtain_parts`
    /// Semaphore. For now, this always returns OK(()) (loops forever retrying in case of errors), but this should be changed
    /// to return Err() if the error is not going to be retryable.
    async fn upload_state_part(self: Arc<Self>, part_idx: u64) -> anyhow::Result<()> {
```

**File:** nearcore/src/state_sync.rs (L439-451)
```rust
        while let Some(result) = tasks.next().await {
            if result.is_err() {
                let _ = sender.send(result);
                // Any remaining upload_state_part() tasks will exit when they read the `canceled` variable,
                // and we'll drop anything still left to be started in `tasks`.
                // However if upload_state_part() doesn't return because it's looping retrying an error, we won't finish
                // dumping this shard's state, and the task will stay around until the `canceled` variable is set when
                // the next epoch starts.
                return;
            }
        }
        let _ = sender.send(Ok(()));
    }
```

**File:** nearcore/src/state_sync.rs (L593-609)
```rust
    fn check_old_progress(
        &self,
        epoch_id: &EpochId,
        dump: &mut DumpState,
        senders: &mut HashMap<ShardId, oneshot::Sender<anyhow::Result<()>>>,
    ) -> anyhow::Result<()> {
        for res in self.chain.chain_store().iter_state_sync_dump_progress() {
            let (shard_id, (dumped_epoch_id, done)) =
                res.context("failed iterating over stored dump progress")?;
            if &dumped_epoch_id != epoch_id {
                self.chain.chain_store().set_state_sync_dump_progress(shard_id, None);
            } else if done {
                dump.dump_state.remove(&shard_id);
                senders.remove(&shard_id);
            }
        }
        Ok(())
```

**File:** nearcore/src/state_sync.rs (L841-866)
```rust
    async fn check_parts_upload(&mut self) {
        let CurrentDump::InProgress(dump) = &mut self.current_dump else {
            return std::future::pending().await;
        };
        let (shard_id, result) = dump.await_parts_upload().await;

        match result {
            Ok(()) => {
                tracing::info!(target: "state_sync_dump", epoch_id = ?&dump.epoch_id, %shard_id, "shard dump finished");
            }
            Err(error) => {
                tracing::error!(target: "state_sync_dump", epoch_id = ?&dump.epoch_id, %shard_id, ?error, "shard dump failed");
            }
        }

        self.chain.chain_store().set_state_sync_dump_progress(
            shard_id,
            Some(StateSyncDumpProgress::AllDumped {
                epoch_id: dump.epoch_id,
                epoch_height: dump.epoch_height,
            }),
        );

        if dump.dump_state.is_empty() {
            self.current_dump = CurrentDump::Done(dump.epoch_id);
        }
```

**File:** core/primitives/src/state_sync.rs (L365-369)
```rust
    AllDumped {
        /// The dumped state corresponds to the state at the beginning of the specified epoch.
        epoch_id: EpochId,
        epoch_height: EpochHeight,
    } = 0,
```
