### Title
L1 Provider Silently Drops `rejected_txs` During Catch-Up, Permanently Marking Rejected L1 Handler Transactions as Committed — (`crates/apollo_l1_provider/src/l1_provider.rs`)

---

### Summary

When `L1Provider` is in `CatchingUp` state, the `rejected_txs` argument passed to `commit_block` is silently discarded. Every consumed L1 handler transaction — including those that failed execution on L2 — is therefore moved to the permanent `Committed` state instead of the retriable `Rejected` state. The L1 message is recorded as successfully processed while the corresponding L2 state change never occurred, and the transaction can never be re-proposed.

---

### Finding Description

`L1Provider::commit_block` accepts three parameters: `committed_txs` (all consumed L1 handler hashes), `rejected_txs` (the subset that failed execution), and `height`. [1](#0-0) 

When the provider is in `CatchingUp` state the call is immediately rerouted:

```rust
if self.state.is_catching_up() {
    return self.accept_commit_while_catching_up(committed_txs, height);
}
```

`rejected_txs` is not forwarded. `accept_commit_while_catching_up` only accepts `committed_txs` and `new_height`: [2](#0-1) 

Inside that function, `apply_commit_block` is always called with an empty set for `rejected_txs`:

```rust
// TODO(guyn): check what about rejected txs here and in the backlog?
Equal => self.apply_commit_block(committed_txs, Default::default()),
```

and for every backlogged block:

```rust
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
``` [3](#0-2) 

`apply_commit_block` partitions `consumed_txs` by membership in `rejected_txs`:

```rust
fn apply_commit_block(&mut self, consumed_txs: ..., rejected_txs: ...) {
    let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
        consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
    self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
    ...
}
``` [4](#0-3) 

Because `rejected_txs` is always empty during catch-up, every consumed transaction — including those that actually failed on L2 — is placed in the `committed_txs` bucket and moved to the permanent `Committed` state.

The batcher constructs the two sets correctly before calling the L1 provider:

```rust
let rejected_l1_handler_tx_hashes = rejected_tx_hashes
    .iter()
    .copied()
    .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
    .collect();

self.l1_provider_client
    .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
    .await;
``` [5](#0-4) 

The information is present at the call site; it is lost inside the L1 provider's catch-up branch.

---

### Impact Explanation

In the normal (non-catch-up) flow a rejected L1 handler transaction is placed in `Rejected` state and returns `ValidationStatus::Validated` when queried, allowing it to be re-proposed in a future block: [6](#0-5) 

After the catch-up misclassification the same transaction is in `Committed` state and returns `ValidationStatus::Invalid(AlreadyIncludedOnL2)`: [7](#0-6) 

The L1 message is permanently recorded as consumed-and-committed while the corresponding L2 state change never occurred. The transaction can never be retried. This is a wrong authoritative L1-message state that persists across restarts and is observable through the RPC and proof pipeline.

This is the direct sequencer analog of the external report: the "disabled market" is the `CatchingUp` state that strips the `rejected_txs` signal; the "user cannot close" is the inability to re-propose the rejected L1 handler; the "liquidation still proceeds" is the L1 message being permanently consumed.

---

### Likelihood Explanation

`CatchingUp` is entered whenever `commit_block` arrives with a height that does not match `current_height` — a routine occurrence after any restart, reorg, or batcher/provider height divergence. The in-code TODO comment (`// TODO(guyn): check what about rejected txs here and in the backlog?`) confirms the gap is known but unresolved. Any block that contains at least one rejected L1 handler transaction and is committed while the provider is catching up will trigger the misclassification.

---

### Recommendation

1. Add `rejected_txs: IndexSet<TransactionHash>` as a parameter to `accept_commit_while_catching_up`.
2. Pass the actual `rejected_txs` from `commit_block` instead of dropping it.
3. Store `rejected_txs` alongside `committed_txs` in the catch-up backlog struct so that backlogged blocks are also applied correctly.
4. Remove the `Default::default()` placeholders in both `apply_commit_block` call sites inside `accept_commit_while_catching_up`.

---

### Proof of Concept

```
1. Create an L1Provider with tx1 in Pending state, height = BlockNumber(5).

2. Trigger CatchingUp by calling commit_block([], [], BlockNumber(7))
   → provider enters CatchingUp, current_height stays at 5.

3. Call commit_block(
       consumed = {tx1},
       rejected = {tx1},   // tx1 failed execution on L2
       height   = BlockNumber(5)
   )
   → routes to accept_commit_while_catching_up({tx1}, 5)
   → rejected_txs is dropped
   → apply_commit_block({tx1}, {})  ← empty rejected set
   → tx1 is partitioned into committed_txs (not rejected_and_consumed)
   → tx_manager.commit_txs([tx1], [])
   → tx1 moves to Committed state

4. Call validate(tx1, BlockNumber(6))
   → returns ValidationStatus::Invalid(AlreadyIncludedOnL2)
   → tx1 can never be re-proposed

Expected: tx1 should be in Rejected state and return ValidationStatus::Validated.
Actual:   tx1 is in Committed state and permanently blocked from re-inclusion.
          The L1 message is recorded as successfully processed; the L2 state
          change never happened.
```

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L289-316)
```rust
    pub fn commit_block(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        rejected_txs: IndexSet<TransactionHash>,
        height: BlockNumber,
    ) -> L1ProviderResult<()> {
        info!("Committing block to L1 provider at height {}.", height);
        if self.state.is_uninitialized() {
            return Err(L1ProviderError::Uninitialized);
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

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L383-464)
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
```

**File:** crates/apollo_batcher/src/batcher.rs (L885-894)
```rust
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
