### Title
`accept_commit_while_catching_up()` Silently Drops `rejected_txs`, Permanently Marking Rejected L1 Handler Transactions as `Committed` — (`crates/apollo_l1_provider/src/l1_provider.rs`)

---

### Summary

`L1Provider::commit_block()` enforces a single-processing invariant for L1 handler transactions: rejected transactions (included in a block but failed execution) are re-queued as `Pending` so they can be re-proposed. When the provider is in `CatchingUp` state, the call is rerouted to `accept_commit_while_catching_up()`, which silently drops the `rejected_txs` parameter. Every rejected L1 handler transaction processed through this path is permanently marked `Committed` instead of `Rejected`, making it unproposable in all future blocks. A developer TODO at the exact bypass site acknowledges the gap.

---

### Finding Description

`commit_block()` accepts both `committed_txs` and `rejected_txs`:

```rust
pub fn commit_block(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    rejected_txs: IndexSet<TransactionHash>,   // ← caller supplies this
    height: BlockNumber,
) -> L1ProviderResult<()> {
    ...
    if self.state.is_catching_up() {
        return self.accept_commit_while_catching_up(committed_txs, height);
        //                                           ^^^^^^^^^^^^  rejected_txs silently dropped
    }
    // Normal path correctly passes both:
    self.apply_commit_block(committed_txs, rejected_txs);
``` [1](#0-0) 

`accept_commit_while_catching_up` only accepts `committed_txs`. Both the `Equal` branch and the backlog drain call `apply_commit_block` with an empty `rejected_txs`:

```rust
// TODO(guyn): check what about rejected txs here and in the backlog?
Equal => self.apply_commit_block(committed_txs, Default::default()),
...
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
``` [2](#0-1) 

`apply_commit_block` partitions `consumed_txs` by membership in `rejected_txs`. With `rejected_txs` always empty, every hash in `consumed_txs` — including the rejected ones — flows into `commit_txs` as a committed transaction:

```rust
fn apply_commit_block(
    &mut self,
    consumed_txs: IndexSet<TransactionHash>,
    rejected_txs: IndexSet<TransactionHash>,
) {
    let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
        consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
    self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
``` [3](#0-2) 

`mark_committed()` sets `state = Committed` and `committed = true`, and asserts the flag was not already set:

```rust
pub fn mark_committed(&mut self) {
    assert!(!self.committed, "L1 handler transaction {} committed twice ...", ...);
    self.state = TransactionState::Committed;
    self.committed = true;
}
``` [4](#0-3) 

`is_validatable()` returns `false` for committed transactions, permanently blocking re-proposal:

```rust
pub fn is_validatable(&self) -> bool {
    !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
}
``` [5](#0-4) 

The batcher correctly computes and passes `rejected_l1_handler_tx_hashes` to `commit_block`:

```rust
let rejected_l1_handler_tx_hashes = rejected_tx_hashes
    .iter()
    .copied()
    .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
    .collect();
self.l1_provider_client
    .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
    .await;
``` [6](#0-5) 

The data is present at the call site; it is dropped inside `commit_block` before reaching `accept_commit_while_catching_up`.

---

### Impact Explanation

Any L1 handler transaction that (a) was included in a block, (b) failed execution (rejected), and (c) was committed while the L1 provider was in `CatchingUp` state will be permanently recorded as `TransactionState::Committed`. Subsequent calls to `validate()` for that hash return `InvalidValidationStatus::AlreadyIncludedOnL2`, and `get_txs()` will never surface it again. The transaction is silently and permanently excluded from all future blocks. This matches the **High** impact: the L1 provider (the admission component for L1 handler transactions) incorrectly rejects valid transactions before sequencing by treating them as already finalized.

---

### Likelihood Explanation

`CatchingUp` state is entered during normal operation whenever `commit_block` arrives with a height that does not match `current_height` — for example, after a node restart, during initial sync, or when the batcher races ahead of the provider. L1 handler transaction rejection is also a normal operational event (e.g., insufficient fee, contract revert). The two conditions co-occur in any deployment that experiences provider lag alongside L1 handler execution failures.

---

### Recommendation

Pass `rejected_txs` through `accept_commit_while_catching_up` and store it alongside `committed_txs` in the `CommitBlockBacklog` entry. Apply the correct `rejected_txs` when draining the backlog:

```rust
fn accept_commit_while_catching_up(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    rejected_txs: IndexSet<TransactionHash>,   // ← add parameter
    new_height: BlockNumber,
) -> L1ProviderResult<()> {
    ...
    Equal => self.apply_commit_block(committed_txs, rejected_txs),
    Greater => {
        self.catchupper.add_commit_block_to_backlog(committed_txs, rejected_txs, new_height);
        ...
    }
    ...
    for committed_block in backlog {
        self.apply_commit_block(committed_block.committed_txs, committed_block.rejected_txs);
    }
```

---

### Proof of Concept

```
1. L1 provider starts at height N, state = Pending.
2. Batcher calls commit_block(consumed={tx_A}, rejected={tx_A}, height=N+2).
   → height mismatch → provider enters CatchingUp, returns Err.
3. Batcher calls commit_block(consumed={tx_A}, rejected={tx_A}, height=N).
   → state.is_catching_up() == true
   → accept_commit_while_catching_up({tx_A}, N) called; rejected_txs dropped.
   → new_height == current_height → apply_commit_block({tx_A}, {}) called.
   → tx_A.mark_committed() → state=Committed, committed=true.
4. Batcher calls commit_block({}, {}, height=N+1) to advance.
   → provider catches up, transitions to Pending.
5. Batcher calls commit_block({}, {}, height=N+2) to finish catchup.
6. Provider is now at height N+3, state=Pending.
7. validate(tx_A, N+3) → is_validatable() == false (committed) →
   returns InvalidValidationStatus::AlreadyIncludedOnL2.
8. tx_A is permanently excluded from all future blocks despite having failed execution.
```

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L289-322)
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

        // If not historical height and not catching up, must go into catchup state upon getting
        // wrong height.
        match self.check_height_with_error(height) {
            Ok(_) => {
                self.apply_commit_block(committed_txs, rejected_txs);
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

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L50-60)
```rust
    pub fn mark_committed(&mut self) {
        // Can't return error because committing only part of a block leaves the provider in an
        // undetermined state.
        assert!(
            !self.committed,
            "L1 handler transaction {} committed twice, this may lead to l2 reorgs,",
            self.tx.tx_hash()
        );
        self.state = TransactionState::Committed;
        self.committed = true;
    }
```

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L179-181)
```rust
    pub fn is_validatable(&self) -> bool {
        !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
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
