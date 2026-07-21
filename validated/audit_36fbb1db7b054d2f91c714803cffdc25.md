### Title
Rejected L1 Handler Transactions Silently Dropped During Catch-Up Due to Incomplete `rejected_txs` Handling — (File: crates/apollo_l1_events/src/l1_events_provider.rs)

---

### Summary

The `L1EventsProvider` catch-up path calls `apply_commit_block` with an empty `rejected_txs` set in two places, causing every L1 handler transaction that was **rejected** during block execution to be permanently recorded as **committed**. Those transactions are never re-proposed, silently disappearing from the sequencer's L1-message pipeline. A TODO comment at the exact branch explicitly acknowledges the gap.

---

### Finding Description

`apply_commit_block` partitions the full set of block-included transactions into committed and rejected subsets, then forwards both to `tx_manager`: [1](#0-0) 

In the normal (non-catch-up) `commit_block` path the caller supplies the real `rejected_txs`: [2](#0-1) 

However, in `accept_commit_while_catching_up`, the `Equal` branch — the branch that actually advances the provider's height — passes `Default::default()` (an empty set) for `rejected_txs`, and the TODO comment at that line explicitly flags the omission: [3](#0-2) 

After catch-up completes, the entire backlog is replayed with the same empty `rejected_txs`: [4](#0-3) 

The backlog is populated by `add_commit_block_to_backlog`, which only stores `committed_txs` and `new_height` — `rejected_txs` is never captured: [5](#0-4) 

Because `rejected_txs` is always empty in both catch-up branches, the partition inside `apply_commit_block` places every transaction into the "committed" bucket. `tx_manager.commit_txs` then marks them all as committed, and they are never re-proposed.

---

### Impact Explanation

An L1 handler transaction that was **rejected** during execution (e.g., due to a revert) should remain eligible for re-inclusion in a later block. After the provider processes that block during catch-up, the transaction is instead recorded as committed. It is removed from the pending pool and will never be sequenced again. This constitutes a wrong L1-message state: the sequencer's internal view of which L1 messages have been consumed diverges from the actual on-chain state, and valid L1→L2 messages are permanently lost from the L2 pipeline.

This matches the **High** impact scope: *"Mempool/gateway/RPC admission… rejects valid transactions before sequencing."*

---

### Likelihood Explanation

Catch-up mode is entered on every node restart, after any crash, and whenever the provider receives a block height that differs from its `current_height`. Any deployment that restarts while rejected L1 handler transactions exist in recent blocks will trigger the bug. No privileged access is required; the trigger is ordinary operational behaviour.

---

### Recommendation

1. Add a `rejected_txs` field to the `CommitBlock` struct stored in the catchupper backlog.
2. Thread the real `rejected_txs` argument through `add_commit_block_to_backlog` and store it.
3. In the `Equal` branch of `accept_commit_while_catching_up`, pass the actual `rejected_txs` received from the caller to `apply_commit_block`.
4. When draining the backlog, pass each entry's stored `rejected_txs` to `apply_commit_block`.
5. Remove the TODO comment once the fix is in place.

---

### Proof of Concept

1. L1 handler transaction `T` is emitted on L1 and scraped by the provider.
2. The sequencer includes `T` in block `N`; `T` reverts during execution and is passed as a member of `rejected_txs` in the `commit_block` call.
3. The node restarts (or the provider falls behind), entering catch-up mode.
4. During catch-up, block `N` arrives at the `Equal` branch of `accept_commit_while_catching_up`.
5. `apply_commit_block(committed_txs, Default::default())` is called; `T` is in `committed_txs` and `rejected_txs` is empty, so `T` is partitioned into the committed bucket.
6. `tx_manager.commit_txs` marks `T` as committed.
7. `T` is never re-proposed. The L1 message it carries is permanently lost from the L2 sequencer's perspective, even though it was never successfully executed on L2. [6](#0-5)

### Citations

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L317-321)
```rust
        match self.check_height_with_error(height) {
            Ok(_) => {
                self.apply_commit_block(committed_txs, rejected_txs);
                self.state = self.state.transition_to_pending();
                Ok(())
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L372-383)
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

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L390-442)
```rust
    fn accept_commit_while_catching_up(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        new_height: BlockNumber,
    ) -> L1EventsProviderResult<()> {
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
                    Err(L1EventsProviderError::UnexpectedHeight {
                        expected_height: current_height,
                        got: new_height,
                    })?
                }
            }
            // TODO(guyn): check what about rejected txs here and in the backlog?
            Equal => self.apply_commit_block(committed_txs, Default::default()),
            // We're still syncing, backlog it, it'll get applied later.
            Greater => {
                self.catchupper.add_commit_block_to_backlog(committed_txs, new_height)?;
                // No need to check the backlog or catchup completion, since those are only
                // applicable if we just increased the provider's height, like in the `Equal` case.
                return Ok(());
            }
        };
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L476-478)
```rust
            for committed_block in backlog {
                self.apply_commit_block(committed_block.committed_txs, Default::default());
            }
```
