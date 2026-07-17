### Title
`check_parts_upload` Unconditionally Marks Shard State as `AllDumped` Regardless of Upload Failure — (`File: nearcore/src/state_sync.rs`)

### Summary

In `nearcore/src/state_sync.rs`, the `StateDumper::check_parts_upload()` function receives the upload result for a shard's state parts but writes `StateSyncDumpProgress::AllDumped` to the persistent store unconditionally — even when the result is `Err`. This mirrors the ERC20WrapperBundler pattern exactly: the return value is inspected only for logging, and execution continues to commit a false "success" state.

### Finding Description

`check_parts_upload()` awaits the completion of all part-upload tasks for one shard and then, regardless of whether those tasks succeeded or failed, persists `StateSyncDumpProgress::AllDumped` for that shard:

```rust
// nearcore/src/state_sync.rs  lines 840-866
async fn check_parts_upload(&mut self) {
    ...
    let (shard_id, result) = dump.await_parts_upload().await;

    match result {
        Ok(()) => { tracing::info!(..., "shard dump finished"); }
        Err(error) => { tracing::error!(..., "shard dump failed"); }   // ← logged only
    }

    // ← runs unconditionally on both Ok and Err
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

The `ShardDump` comment and the `dump_shard_state` sender path confirm that the `Err` branch is intentionally wired and expected to fire once the current infinite-retry workaround is removed:

```
// This will give Ok(()) when they're all done, or Err() when one gives an error
// For now the tasks never fail, since we just retry all errors like the old implementation did,
// but we probably want to make a change to distinguish which errors are actually retryable
``` [2](#0-1) 

`dump_shard_state` already sends the first `Err` result on the channel and returns early, so the channel can carry an error today if `respawn_for_parallelism` propagates a task panic: [3](#0-2) 

### Impact Explanation

`StateSyncDumpProgress::AllDumped` is the persistent commitment that a shard's full state for an epoch is available in external storage. `check_old_progress()` reads this flag on every restart and, when it sees `done == true`, removes the shard from the pending dump set — permanently skipping the re-dump:

```rust
} else if done {
    dump.dump_state.remove(&shard_id);
    senders.remove(&shard_id);
}
``` [4](#0-3) 

`AllDumped` maps to `done = true` in `iter_state_sync_dump_progress`: [5](#0-4) 

Consequence: if a shard upload fails (e.g., external storage quota exceeded, non-retryable I/O error, or task panic), the node permanently records the epoch as fully dumped. Any node that subsequently attempts state sync for that epoch will find the header present but one or more state parts missing, causing `set_state_part` / `apply_state_part` to fail and leaving the syncing node unable to reconstruct the shard state root. [6](#0-5) 

### Likelihood Explanation

Currently `upload_state_part` loops forever on every error, so the `Err` path through `dump_shard_state` → `check_parts_upload` is suppressed in normal operation. However:

1. The TODO comment explicitly states the infinite-retry behavior will be changed to distinguish retryable from non-retryable errors.
2. A task panic (OOM, stack overflow) propagated through `respawn_for_parallelism` can already deliver `Err` today.
3. Once any non-retryable error path is added (the stated intent), the bug activates immediately with no further code change needed.

### Recommendation

Gate the `AllDumped` write on a successful result:

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
        // Leave progress as InProgress so the next restart retries.
    }
}
```

This ensures `AllDumped` is only committed when all parts are verifiably present in external storage, preserving the reconstruction invariant that syncing nodes depend on.

### Proof of Concept

1. Configure a node as a state-sync dumper with an external storage backend that accepts the header but rejects part uploads after N parts (e.g., by injecting a quota error or a panic in `upload_state_part`).
2. Observe that `check_parts_upload` logs `"shard dump failed"` but immediately writes `AllDumped` to `DBCol::BlockMisc`.
3. Restart the node; `check_old_progress` reads `done = true` and skips re-dumping the shard.
4. A second node attempting state sync for that epoch downloads the header successfully, then fails to retrieve the missing parts, and cannot reconstruct the shard state root — state sync is permanently broken for that epoch on that external storage endpoint. [7](#0-6) [8](#0-7)

### Citations

**File:** nearcore/src/state_sync.rs (L183-187)
```rust
    // This will give Ok(()) when they're all done, or Err() when one gives an error
    // For now the tasks never fail, since we just retry all errors like the old implementation did,
    // but we probably want to make a change to distinguish which errors are actually retryable
    // (e.g. the state snapshot isn't ready yet)
    upload_parts: oneshot::Receiver<anyhow::Result<()>>,
```

**File:** nearcore/src/state_sync.rs (L439-450)
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

**File:** nearcore/src/state_sync.rs (L840-866)
```rust
    // Returns when the part upload tasks are finished
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

**File:** chain/chain/src/store/mod.rs (L793-796)
```rust
                    match progress {
                        StateSyncDumpProgress::AllDumped { epoch_id, .. } => (epoch_id, true),
                        StateSyncDumpProgress::InProgress { epoch_id, .. } => (epoch_id, false),
                        StateSyncDumpProgress::Skipped { epoch_id, .. } => (epoch_id, true),
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-560)
```rust
    pub fn set_state_part(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        part_id: PartId,
        part: &StatePart,
    ) -> Result<(), Error> {
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
            StatePartValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(format!(
                "set_state_part failed: validate_state_part failed. state_root={:?}",
                state_root
            )));
        }
        // Saving the part data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id.idx)).unwrap();
        let bytes = part.to_bytes();
        store_update.set(DBCol::StateParts, &key, &bytes);
        store_update.commit();
        Ok(())
```
