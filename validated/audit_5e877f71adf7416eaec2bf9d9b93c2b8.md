### Title
Swallowed `L1EventsProvider::commit_block` Error Causes L1 Handler Transaction State Divergence — (File: `crates/apollo_batcher/src/batcher.rs`)

---

### Summary

In `commit_proposal_and_block`, after writing the committed block to storage, the batcher calls `l1_events_provider_client.commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)` to advance the L1 events provider's internal consumed-transaction ledger. If that call returns an error, the batcher logs the error and increments a metric counter, but **does not propagate the error and returns `Ok(())`**. The batcher then proceeds to build the next block. The L1 events provider's internal height and consumed-transaction set are now stale relative to the committed chain state, creating a window in which already-consumed L1 handler transactions can be re-served to the next block.

---

### Finding Description

`commit_proposal_and_block` in `crates/apollo_batcher/src/batcher.rs` (lines 1118–1143) performs the following sequence:

1. Writes the block to storage via `storage_writer.commit_proposal(...)` — this succeeds and is the point of no return.
2. Calls `l1_events_provider_client.commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)`.
3. If step 2 returns an error (e.g., `L1EventsProviderError::UnexpectedHeight`), the error is matched, logged, and a metric is incremented — but the function **falls through and returns `Ok(())`**. [1](#0-0) 

The L1 events provider's `commit_block` is the only mechanism by which the provider learns which L1 handler transaction hashes were consumed in a finalized block. When this notification is dropped:

- The provider's `current_height` does not advance.
- Consumed transactions remain in the provider's internal "pending" queue.
- The provider enters `CatchingUp` state and begins an asynchronous L2-sync-driven recovery. [2](#0-1) 

During the catch-up window — which spans from the failed `commit_block` call until the L2 sync re-drives the provider past the committed height — the batcher may call `get_txs` on the provider for the next block. Because the provider's consumed set is stale, it may return L1 handler transactions that were already executed in the just-committed block.

The test comment at line 467–468 of `crates/apollo_l1_events/src/l1_events_provider_tests.rs` explicitly acknowledges this design: *"The batcher swallows `commit_block` errors and advances anyway."* [3](#0-2) 

---

### Impact Explanation

An L1 handler transaction that was already executed and committed in block N may be re-served by the stale L1 events provider and included in block N+1. This constitutes **double-execution of an L1 message**: the same L1→L2 message is applied to the Starknet state twice, producing a wrong state root, wrong storage values, and wrong receipts for the affected contracts. This maps to:

> **Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

and

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The catch-up mechanism (`Catchupper`) eventually re-synchronises the provider via L2 sync, but it does not retroactively undo the double-execution that occurred during the catch-up window. Once the wrong block is committed, the state divergence is permanent unless an explicit revert is performed.

---

### Likelihood Explanation

The `UnexpectedHeight` error from `commit_block` is a normal operational condition: it fires whenever the provider's internal height diverges from the batcher's height, which can occur after a node restart, a sync gap, or a transient RPC failure between the batcher and the L1 events provider component. No privileged access or malicious peer is required. The batcher's own test suite pins this behavior with `decision_reached_return_success_when_l1_commit_block_fails`, confirming the path is reachable in production. [4](#0-3) 

---

### Recommendation

Propagate the `commit_block` error from `commit_proposal_and_block` instead of swallowing it. If the L1 events provider is intentionally allowed to lag (for availability reasons), the batcher must not serve L1 handler transactions from the provider until the provider has confirmed it has processed all heights up to and including the current committed height. Concretely:

1. Either return `Err(BatcherError::InternalError)` from `commit_proposal_and_block` when `l1_events_provider_client.commit_block` fails, causing the batcher to halt block production until the provider recovers.
2. Or gate `get_txs` calls to the L1 events provider on a confirmed-height check, refusing to serve transactions for block N+1 until the provider's `current_height` equals N+1.

---

### Proof of Concept

1. Node starts; batcher is at height H; L1 events provider is at height H.
2. Block H is executed; it consumes L1 handler transaction `tx_A`.
3. `commit_proposal_and_block` writes block H to storage (irreversible).
4. `l1_events_provider_client.commit_block({tx_A}, {}, H)` returns `UnexpectedHeight` (e.g., due to a transient restart of the L1 events provider component).
5. The batcher logs the error, increments `BATCHER_L1_EVENTS_PROVIDER_ERRORS`, and returns `Ok(())`.
6. The L1 events provider enters `CatchingUp` state; `tx_A` remains in its pending queue.
7. The batcher begins building block H+1 and calls `get_txs` on the provider.
8. The provider, still at height H with `tx_A` pending, returns `tx_A` again.
9. Block H+1 is executed with `tx_A` included — double-execution of the L1 message.
10. The resulting state root for block H+1 is wrong; all downstream commitments, proofs, and RPC responses derived from it are incorrect. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_batcher/src/batcher.rs (L1065-1158)
```rust
    async fn commit_proposal_and_block(
        &mut self,
        height: BlockNumber,
        state_diff: ThinStateDiff,
        address_to_nonce: HashMap<ContractAddress, Nonce>,
        consumed_l1_handler_tx_hashes: IndexSet<TransactionHash>,
        rejected_tx_hashes: IndexSet<TransactionHash>,
        storage_commitment_block_hash: StorageCommitmentBlockHash,
    ) -> BatcherResult<()> {
        info!(
            "Committing block at height {} and notifying mempool & L1 event provider of the block.",
            height
        );
        trace!("Rejected transactions: {:#?}, State diff: {:#?}.", rejected_tx_hashes, state_diff);

        // Proposal commitment is the the partial block hash when it's available, and None
        // otherwise. The commitment is computed here to set the prev_proposal_commitment cache. As
        // this cache is only used for blocks obtained through the decision_reached flow, it can
        // be None for old (pre 0.13.2) blocks.
        let proposal_commitment = match &storage_commitment_block_hash {
            StorageCommitmentBlockHash::Partial(components) => Some((
                height,
                ProposalCommitment {
                    partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
                        components,
                    )
                    .map_err(|e| {
                        error!("Failed to compute partial block hash: {}", e);
                        BatcherError::InternalError
                    })?,
                },
            )),
            StorageCommitmentBlockHash::ParentHash(_) => None,
        };

        // Commit the proposal to the storage.
        self.storage_writer
            .commit_proposal(height, state_diff, storage_commitment_block_hash)
            .map_err(|err| {
                error!("Failed to commit proposal to storage: {}", err);
                BatcherError::InternalError
            })?;
        info!("Successfully committed proposal for block {} to storage.", height);

        self.prev_proposal_commitment = proposal_commitment;

        // Notify the L1 provider of the new block.
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_events_provider_result = self
            .l1_events_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
            .await;

        // Return error if the commit to the L1 provider failed.
        if let Err(err) = l1_events_provider_result {
            match err {
                L1EventsProviderClientError::L1EventsProviderError(
                    L1EventsProviderError::UnexpectedHeight { expected_height, got },
                ) => {
                    error!(
                        "Unexpected height while committing block in L1 provider: expected={:?}, \
                         got={:?}",
                        expected_height, got
                    );
                }
                other_err => {
                    error!(
                        "Unexpected error while committing block in L1 provider: {:?}",
                        other_err
                    );
                }
            }
            BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
        }

        // Notify the mempool of the new block (skipped in validation-only mode).
        if let Some(mempool_client) = &self.mempool_client {
            let mempool_result = mempool_client
                .commit_block(CommitBlockArgs { address_to_nonce, rejected_tx_hashes })
                .await;
            if let Err(mempool_err) = mempool_result {
                // Recoverable error, mempool won't be updated with the new block.
                error!("Failed to commit block to mempool: {}", mempool_err);
            }
        }

        BUILDING_HEIGHT.increment(1);
        Ok(())
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L286-350)
```rust
    pub fn commit_block(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        rejected_txs: IndexSet<TransactionHash>,
        height: BlockNumber,
    ) -> L1EventsProviderResult<()> {
        info!("Committing block to L1 provider at height {}.", height);
        if self.state.is_uninitialized() {
            return Err(L1EventsProviderError::Uninitialized);
        }

        if self.is_historical_height(height) {
            debug!(
                "Skipping commit block for height: {height}, it is lower than start_height: {}. \
                 Current height is {}.",
                self.start_height
                    .expect("is_historic_height returns false if start_height is not set"),
                self.current_height
            );
            return Ok(());
        }

        // Reroute this block to catchupper, either adding it to the backlog, or applying it and
        // ending the catchup.
        if self.state.is_catching_up() {
            // Once catchup completes it will transition to Pending state by itself.
            return self.accept_commit_while_catching_up(committed_txs, height);
        }

        // If not historical height and not catching up, must go into catchup state upon getting
        // wrong height.
        match self.check_height_with_error(height) {
            Ok(_) => {
                self.apply_commit_block(committed_txs, rejected_txs);
                self.state = self.state.transition_to_pending();
                Ok(())
            }
            Err(err) => {
                // We are returning an error -> not accepting the block with this height. In order
                // to to be able to serve future requests, we must catch up to it, and finish
                // catching up when the provider has synced this height.
                if self.state.is_uninitialized() {
                    warn!(
                        "Provider received a block height ({height}) while it is uninitialized. \
                         Cannot start catching up until getting the start_height from the scraper \
                         during the initialize call."
                    );
                } else {
                    info!(
                        "Provider received a block_height ({height}) that is different than the \
                         current height ({}), starting catch-up process.",
                        self.current_height
                    );
                    // TODO(guyn): in case block_height is lower than current_height, should we
                    // still go to catchup? Perhaps it is better to return an
                    // error and let the batcher keep going without the Provider?
                    // Do we need to check that the blocks getting committed are consistent with the
                    // provider records? Can we just accept the block without an
                    // error if we do that check?
                    self.start_catching_up(height);
                }
                Err(err)
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider_tests.rs (L465-495)
```rust
// Regression test for the cap's recovery path (L-16 review).
//
// The batcher swallows `commit_block` errors and advances anyway (batcher.rs commit-block
// handling is pinned by `decision_reached_return_success_when_l1_commit_block_fails`), so an
// overflow height rejected by the provider is dropped downstream. Before the fix, the *next* tip
// commit then tripped the "Heights should be sequential." assert and panicked the provider,
// turning bounded memory into a crash loop -- the opposite of the intended degraded-but-recoverable
// behavior. After the fix, overflow abandons the in-memory backlog and lets L2 sync re-drive the
// range, so the follow-up commit is handled gracefully.
#[test]
fn commit_after_overflow_does_not_panic() {
    const CAP: usize = 3;
    let mut l1_events_provider = provider_catching_up_with_backlog_cap(CAP);

    // Fill the backlog exactly to the cap (heights 1..=3, all above current height 0).
    for height in [BlockNumber(1), BlockNumber(2), BlockNumber(3)] {
        commit_block_no_rejected(&mut l1_events_provider, &[], height);
    }

    // Overflow commit. Mimic the batcher, which swallows the result and advances regardless.
    let _overflow_result = l1_events_provider.commit_block([].into(), [].into(), BlockNumber(4));

    // The next tip commit must be handled gracefully; pre-fix this panicked on the sequentiality
    // assert in `add_commit_block_to_backlog`.
    let result = l1_events_provider.commit_block([].into(), [].into(), BlockNumber(5));
    assert_matches!(result, Ok(()));

    // Overflow abandoned the backlog (memory bounded) and extended the sync target to the latest
    // tip so L2 sync re-drives the whole range authoritatively.
    assert!(l1_events_provider.catchupper.commit_block_backlog.is_empty());
    assert_eq!(l1_events_provider.catchupper.target_height(), BlockNumber(5));
```
