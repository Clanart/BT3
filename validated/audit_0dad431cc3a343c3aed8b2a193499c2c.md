### Title
Rejected L1 Handler Transactions Permanently Marked as Committed During Catch-Up, Causing Permanent L1 Message Loss — (`File: crates/apollo_l1_provider/src/catchupper.rs`)

---

### Summary

When the `L1Provider` enters catch-up mode, it replays historical blocks via `l2_sync_task`. The `SyncBlock` type carries no field for rejected L1 handler transaction hashes. The catch-up path therefore always passes an empty `rejected_txs` set to `apply_commit_block`. As a result, every L1 handler transaction that was **rejected** in a historical block is incorrectly recorded as `Committed` instead of `Rejected` in the `TransactionManager`. A `Committed` record returns `AlreadyIncludedOnL2` on the next `validate()` call, permanently blocking re-proposal of the message. Any node that restarts and catches up will disagree with a proposer that correctly re-includes the rejected L1 handler, causing consensus failure or silent permanent loss of the L1 message.

---

### Finding Description

**Root cause — `SyncBlock` carries no rejected-tx field:**

`SyncBlock` is defined with only `l1_transaction_hashes` (all consumed L1 handler txs) and no field for the rejected subset. [1](#0-0) 

**Root cause — `l2_sync_task` hardcodes empty rejected set:**

The async catch-up task that replays blocks from state sync explicitly sets `l1_handler_rejected_tx_hashes = Default::default()` with the comment "No rejected txs in sync blocks." [2](#0-1) 

**Propagation — `accept_commit_while_catching_up` also drops rejected info:**

Both the `Equal` branch (sync-driven commit) and the backlog drain call `apply_commit_block` with `Default::default()` for `rejected_txs`. [3](#0-2) 

**Effect in `apply_commit_block`:**

The partition `consumed_txs.iter().partition(|tx| rejected_txs.contains(tx))` places every consumed tx into `committed_txs` when `rejected_txs` is empty. All are then passed to `commit_txs` as committed. [4](#0-3) 

**Effect in `commit_txs`:**

`mark_committed()` is called for every consumed tx; `mark_rejected()` is never called for any of them. [5](#0-4) 

**Wrong validation result:**

`validate_tx` checks `record.is_validatable()`. A `Committed` record is not validatable and returns `AlreadyIncludedOnL2`, while a `Rejected` record is validatable and returns `Validated` (allowing re-proposal). [6](#0-5) 

This is confirmed by the test `validate_rejected_transactions`, which asserts that `tx_hash!(1)` (a rejected tx) returns `ValidationStatus::Validated`, while `tx_hash!(2)` (a committed tx) returns `AlreadyIncludedOnL2`. [7](#0-6) 

---

### Impact Explanation

After catch-up, the `L1Provider`'s `TransactionManager` holds stale `Committed` records for every L1 handler transaction that was rejected in any historical block. When the proposer (which went through the normal `commit_block` path with correct `rejected_txs`) re-includes such a transaction in the next block, the validator (which went through catch-up) calls `validate()` and receives `AlreadyIncludedOnL2`. The validator therefore marks the transaction as invalid, causing a consensus disagreement on a legitimately proposed block. Alternatively, if the proposer also went through catch-up, the rejected L1 message is silently dropped forever — the L1→L2 message is never executed on L2.

This matches: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing** (the L1 Provider rejects a valid, re-proposable L1 handler transaction by returning `AlreadyIncludedOnL2`).

---

### Likelihood Explanation

The trigger requires two ordinary conditions that co-occur in production:

1. An L1 handler transaction is rejected during block execution (e.g., the target contract reverts). This is a normal operational event.
2. The sequencer node restarts or experiences a height mismatch, causing the `L1Provider` to enter `CatchingUp` state. This is also a normal operational event (startup, crash recovery).

No privileged access or malicious peer is required. Any user who can send an L1→L2 message that will fail execution can trigger condition 1; condition 2 is triggered by normal node lifecycle events.

---

### Recommendation

1. **Short term:** Add a `rejected_l1_transaction_hashes: Vec<TransactionHash>` field to `SyncBlock` and populate it from storage (the batcher already records which L1 handler txs were rejected via `commit_proposal`). Pass this field through `l2_sync_task` and `accept_commit_while_catching_up` to `apply_commit_block`.

2. **Short term (mitigation):** Until the field is added, document that nodes recovering from catch-up may permanently lose rejected L1 handler transactions and require a full resync from genesis to recover correct state.

3. **Long term:** Add an invariant test asserting that after catch-up over a block containing a rejected L1 handler tx, the tx's state in `TransactionManager` is `Rejected` (not `Committed`).

---

### Proof of Concept

```
1. Deploy an L1→L2 message whose target contract always reverts.
2. The sequencer includes the L1 handler tx in block N; it is rejected.
   - Batcher calls: commit_block(consumed={tx_hash}, rejected={tx_hash}, height=N)
   - TransactionManager correctly marks tx_hash as Rejected.
3. Restart the sequencer node. L1Provider starts in Uninitialized state.
4. Batcher calls commit_block(height=N+1) before L1Provider is ready.
   - L1Provider enters CatchingUp state, starts l2_sync_task from height=0.
5. l2_sync_task fetches SyncBlock for block N:
   - SyncBlock.l1_transaction_hashes = [tx_hash]  (no rejected field)
   - Calls: commit_block(consumed={tx_hash}, rejected={}, height=N)
6. apply_commit_block partitions: committed_txs=[tx_hash], rejected_and_consumed=[]
   - commit_txs marks tx_hash as Committed.
7. Proposer re-includes tx_hash in block N+2 (it was rejected, so it should be re-proposed).
8. Validator (post-catch-up) calls validate(tx_hash):
   - record.state == Committed → returns AlreadyIncludedOnL2 (Invalid)
9. Validator rejects the proposer's block → consensus failure.
   OR: if proposer also went through catch-up, tx_hash is never re-proposed → L1 message permanently lost.
``` [8](#0-7) [9](#0-8) [1](#0-0)

### Citations

**File:** crates/apollo_state_sync_types/src/state_sync_types.rs (L17-27)
```rust
pub struct SyncBlock {
    pub state_diff: ThinStateDiff,
    // TODO(Matan): decide if we want block hash, parent block hash and full classes here.
    pub account_transaction_hashes: Vec<TransactionHash>,
    pub l1_transaction_hashes: Vec<TransactionHash>,
    pub block_header_without_hash: BlockHeaderWithoutHash,
    /// The commitments are required to calculate the partial block hash.
    /// In Starknet versions prior to 0.13.2, the commitments are not included in the block header.
    /// Therefore, it is optional.
    pub block_header_commitments: Option<BlockHeaderCommitments>,
}
```

**File:** crates/apollo_l1_provider/src/catchupper.rs (L149-182)
```rust
async fn l2_sync_task(
    l1_provider_client: SharedL1ProviderClient,
    sync_client: SharedStateSyncClient,
    mut current_height: BlockNumber,
    target_height: BlockNumber,
    retry_interval: Duration,
) {
    while current_height <= target_height {
        // TODO(Gilad): add tracing instrument.
        debug!(
            "Syncing L1Provider with L2 height: {} to target height: {}",
            current_height, target_height
        );
        let block = sync_client.get_block(current_height).await.inspect_err(|err| debug!("{err}"));

        match block {
            Ok(block) => {
                // No rejected txs in sync blocks.
                let l1_handler_rejected_tx_hashes = Default::default();

                l1_provider_client
                    .commit_block(
                        block.l1_transaction_hashes.into_iter().collect(),
                        l1_handler_rejected_tx_hashes,
                        current_height,
                    )
                    .await
                    .unwrap();
                current_height = current_height.unchecked_next();
            }
            _ => tokio::time::sleep(retry_interval).await,
        }
    }
}
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L365-376)
```rust
    fn apply_commit_block(
        &mut self,
        consumed_txs: IndexSet<TransactionHash>,
        rejected_txs: IndexSet<TransactionHash>,
    ) {
        debug!("Applying commit_block to height: {}", self.current_height);
        let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
            consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
        self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);

        self.current_height = self.current_height.unchecked_next();
    }
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L383-465)
```rust
    fn accept_commit_while_catching_up(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        new_height: BlockNumber,
    ) -> L1ProviderResult<()> {
        let current_height = self.current_height;
        debug!(
            "Catchupper processing commit-block at height: {new_height}, current height is \
             {current_height}"
        );
        match new_height.cmp(&current_height) {
            // This is likely a bug in the batcher/sync, it should never be _behind_ the provider.
            Less => {
                // TODO(guyn): check if this is reliable: old blocks can have txs that were
                // committed then consumed and deleted. We should probably decide to always log and
                // ignore old blocks or always return an error.
                let diff_from_already_committed: Vec<_> = committed_txs
                    .iter()
                    .copied()
                    .filter(|&tx_hash| !self.tx_manager.is_committed(tx_hash))
                    .collect();

                if diff_from_already_committed.is_empty() {
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, and all committed transaction hashes already known to be \
                         committed."
                    );
                    return Ok(());
                } else {
                    // This is either a configuration error or a bug in the
                    // batcher/sync/catching up code.
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, with DIFFERENT transaction_hashes: \
                         {diff_from_already_committed:?}"
                    );
                    Err(L1ProviderError::UnexpectedHeight {
                        expected_height: current_height,
                        got: new_height,
                    })?
                }
            }
            // TODO(guyn): check what about rejected txs here and in the backlog?
            Equal => self.apply_commit_block(committed_txs, Default::default()),
            // We're still syncing, backlog it, it'll get applied later.
            Greater => {
                self.catchupper.add_commit_block_to_backlog(committed_txs, new_height);
                // No need to check the backlog or catchup completion, since those are only
                // applicable if we just increased the provider's height, like in the `Equal` case.
                return Ok(());
            }
        };

        // If caught up, apply the backlog and transition to Pending.
        // Note that at this point self.current_height is already incremented to the next height, it
        // is one more than the latest block that was committed.
        if self.catchupper.is_caught_up(self.current_height) {
            info!(
                "Catch up sync completed, provider height is now {}, processing backlog...",
                self.current_height
            );
            let backlog = std::mem::take(&mut self.catchupper.commit_block_backlog);
            assert!(
                backlog.is_empty()
                    || self.current_height == backlog.first().unwrap().height
                        && backlog
                            .windows(2)
                            .all(|height| height[1].height == height[0].height.unchecked_next()),
                "Backlog must have sequential heights starting sequentially after current height: \
                 {}, backlog: {:?}",
                self.current_height,
                backlog.iter().map(|commit_block| commit_block.height).collect::<Vec<_>>()
            );

            info!(
                "Applying commit-block backlog for heights: {:?}",
                backlog.iter().map(|commit_block| commit_block.height).collect::<Vec<_>>()
            );

            for committed_block in backlog {
                self.apply_commit_block(committed_block.committed_txs, Default::default());
            }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L116-145)
```rust
    pub fn validate_tx(&mut self, tx_hash: TransactionHash, unix_now: u64) -> ValidationStatus {
        let current_staging_epoch_cloned = self.current_staging_epoch;

        let policy = TransactionRecordPolicy {
            cancellation_timelock: self.config.l1_handler_cancellation_timelock_seconds,
        };

        let validation_status = self.with_record(tx_hash, |record| {
            // If the current time affects the state, update state now.
            record.update_time_based_state(unix_now, policy);
            if !record.is_validatable() {
                match record.state {
                    TransactionState::Committed => {
                        InvalidValidationStatus::AlreadyIncludedOnL2.into()
                    }
                    TransactionState::CancelledOnL2 => {
                        InvalidValidationStatus::CancelledOnL2.into()
                    }
                    TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1.into(),
                    _ => unreachable!(),
                }
            } else if record.try_mark_staged(current_staging_epoch_cloned) {
                ValidationStatus::Validated
            } else {
                InvalidValidationStatus::AlreadyIncludedInProposedBlock.into()
            }
        });

        validation_status.unwrap_or(InvalidValidationStatus::NotFound.into())
    }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L147-164)
```rust
    pub fn commit_txs(
        &mut self,
        committed_txs: &[TransactionHash],
        rejected_txs: &[TransactionHash],
    ) {
        self.rollback_staging();

        for &tx_hash in committed_txs {
            self.create_record_if_not_exist(tx_hash);
            self.with_record(tx_hash, |r| r.mark_committed()).unwrap();
        }
        for &tx_hash in rejected_txs {
            self.with_record(tx_hash, |r| r.mark_rejected()).expect(
                "Storage inconsistency: a transaction sent to the batcher was removed \
                 unexpectedly.",
            );
        }
    }
```

**File:** crates/apollo_l1_provider/src/l1_provider_tests.rs (L501-512)
```rust
#[rstest]
#[case::validate_rejected_transaction(tx_hash!(1), ValidationStatus::Validated)]
#[case::validate_non_rejected_transaction(tx_hash!(2), ValidationStatus::Invalid(InvalidValidationStatus::AlreadyIncludedOnL2))]
#[case::validate_uncommitted_transaction(tx_hash!(3), ValidationStatus::Validated)]
fn validate_rejected_transactions(
    #[case] tx: TransactionHash,
    #[case] expected_status: ValidationStatus,
) {
    let mut l1_provider = setup_rejected_transactions();

    assert_eq!(l1_provider.validate(tx, BlockNumber(1)).unwrap(), expected_status);
}
```
