### Title
Rejected L1 Handler Transactions Never Cleared During Catch-Up, Enabling Re-Execution - (File: crates/apollo_l1_provider/src/l1_provider.rs)

### Summary

When the `L1Provider` is in `CatchingUp` state, the `rejected_txs` parameter is silently dropped at the routing call site and replaced with an empty set in every `apply_commit_block` invocation. Rejected L1 handler transactions therefore remain in `Pending` state inside the `proposable_index` and are eligible for re-proposal in future blocks, causing wrong execution state.

### Finding Description

`L1Provider::commit_block` routes to `accept_commit_while_catching_up` when the provider is catching up, but drops `rejected_txs` entirely:

```rust
// crates/apollo_l1_provider/src/l1_provider.rs:313-315
if self.state.is_catching_up() {
    return self.accept_commit_while_catching_up(committed_txs, height);
}
``` [1](#0-0) 

Inside `accept_commit_while_catching_up`, every call to `apply_commit_block` passes `Default::default()` (an empty `IndexSet`) as the rejected set:

```rust
// Equal branch (line 427)
Equal => self.apply_commit_block(committed_txs, Default::default()),
// Backlog replay (line 464)
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
``` [2](#0-1) 

The code itself acknowledges this with a TODO:

```rust
// TODO(guyn): check what about rejected txs here and in the backlog?
Equal => self.apply_commit_block(committed_txs, Default::default()),
``` [3](#0-2) 

`apply_commit_block` calls `tx_manager.commit_txs(&committed_txs, &rejected_and_consumed)`, where `rejected_and_consumed` is derived from the intersection of `consumed_txs` and `rejected_txs`. With `rejected_txs` always empty, `mark_rejected()` is never called for any transaction during catch-up: [4](#0-3) 

`commit_txs` in `TransactionManager` only marks a transaction as `Rejected` when it appears in the `rejected_txs` slice: [5](#0-4) 

Transactions that are not marked `Rejected` remain in `Pending` state and stay in the `proposable_index`, making them eligible for `get_txs` in future block proposals: [6](#0-5) 

The batcher correctly computes and sends `rejected_l1_handler_tx_hashes` to the L1 provider on every `commit_block` call: [7](#0-6) 

That information is simply discarded when the provider is in `CatchingUp` state.

### Impact Explanation

An L1 handler transaction that was rejected during execution (e.g., because it failed a validity check or caused a revert) is never removed from the `proposable_index`. In the next block proposal after catch-up completes, `get_txs` returns it again. The validator calls `validate_tx` on it and receives `ValidationStatus::Validated` because the record is still `Pending`. The blockifier then re-executes the transaction, producing wrong execution state, wrong receipts, and wrong events for that block. For L1→L2 message bridging transactions this can mean double-minting or double-crediting assets.

This matches the impact category: **Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input (Critical)** and **Mempool/gateway/RPC admission accepts invalid transactions before sequencing (High)**.

### Likelihood Explanation

The `CatchingUp` state is entered automatically whenever the batcher sends a `commit_block` with a height that differs from the provider's `current_height`. This is a routine operational event: it occurs on every node restart, after any crash recovery, or whenever the provider falls behind the batcher. No privileged access or malicious peer is required. Any block that contains a rejected L1 handler transaction during a catch-up window triggers the bug.

### Recommendation

Pass `rejected_txs` through to `accept_commit_while_catching_up` and store it alongside `committed_txs` in the backlog so it can be applied when the block is replayed:

1. Change `accept_commit_while_catching_up` to accept `rejected_txs: IndexSet<TransactionHash>` as a second parameter.
2. In the `Equal` branch, call `self.apply_commit_block(committed_txs, rejected_txs)` instead of `Default::default()`.
3. Store `rejected_txs` in `CommitBlockBacklogEntry` alongside `committed_txs` so the backlog replay at line 463–465 can also pass the correct rejected set.
4. In `commit_block`, pass `rejected_txs` to `accept_commit_while_catching_up` instead of dropping it.

### Proof of Concept

```
1. Start L1Provider at height 5 with L1 handler tx T1 in Pending state.
2. Batcher sends commit_block(consumed={T1}, rejected={T1}, height=7).
   → height 7 ≠ current_height 5, so provider enters CatchingUp state.
   → rejected_txs={T1} is dropped; accept_commit_while_catching_up({T1}, 7) is called.
3. Batcher sends commit_block({}, {}, height=5).
   → Equal branch: apply_commit_block({T1}, Default::default()).
   → T1 is marked Committed (not Rejected). current_height becomes 6.
4. Batcher sends commit_block({}, {}, height=6).
   → Equal branch: apply_commit_block({}, Default::default()). current_height becomes 7.
   → is_caught_up(7) == true; backlog (empty) is applied; state → Pending.
5. Next proposal: get_txs() returns T1 (still Pending in proposable_index).
6. T1 is included in the new block and re-executed by the blockifier,
   producing wrong state/receipts/events for a transaction that was already rejected.
``` [8](#0-7)

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L311-316)
```rust
        // Reroute this block to catchupper, either adding it to the backlog, or applying it and
        // ending the catchup.
        if self.state.is_catching_up() {
            // Once catchup completes it will transition to Pending state by itself.
            return self.accept_commit_while_catching_up(committed_txs, height);
        }
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L364-376)
```rust
    /// Commit the given transactions, and increment the current height.
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

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L383-477)
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

            info!(
                "Catch up done: commit-block backlog was processed, now transitioning to Pending \
                 state at new height: {}.",
                self.current_height
            );

            self.state = ProviderState::Pending;
        }

        Ok(())
    }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L72-113)
```rust
    pub fn get_txs(&mut self, n_txs: usize, now: u64) -> Vec<L1HandlerTransaction> {
        // Oldest        Now.sub(timelock)     Newest       Now
        //  |<---  passed  --->|                 |           |
        //  |<--- cooldown --->|                 |           |
        // t-------------------------------------------------->
        let cutoff = now.saturating_sub(self.config.l1_handler_proposal_cooldown_seconds.as_secs());
        let past_cooldown_txs = self.proposable_index.range(..cutoff);

        // Linear scan, but we expect this to be a small number of transactions (< 10 roughly).
        let unstaged_tx_hashes: Vec<_> = past_cooldown_txs
            .flat_map(|(_timestamp, tx_hashes)| tx_hashes.iter())
            .skip_while(|&&tx_hash| self.is_staged(tx_hash))
            .take(n_txs)
            .copied()
            .collect();

        for &tx_hash in unstaged_tx_hashes.iter() {
            let record = self.records.get(&tx_hash).expect("transaction should exist");
            assert_eq!(
                record.state,
                TransactionState::Pending,
                "Transaction {tx_hash} has state {:?}. Only Pending transactions should be in the \
                 proposable index.",
                record.state
            );
        }

        let mut txs = Vec::with_capacity(n_txs);
        let current_staging_epoch = self.current_staging_epoch; // borrow-checker constraint.
        for tx_hash in unstaged_tx_hashes {
            let newly_staged =
                self.with_record(tx_hash, |record| record.try_mark_staged(current_staging_epoch));
            assert_eq!(
                newly_staged,
                Some(true),
                "Inconsistent storage state: indexed l1 handler {tx_hash} is not in storage or \
                 wasn't marked as staged."
            );

            txs.push(self.records[&tx_hash].get_unchecked().clone());
        }
        txs
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

**File:** crates/apollo_batcher/src/batcher.rs (L884-894)
```rust
        // Notify the L1 provider of the new block.
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_provider_result = self
            .l1_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
            .await;
```
