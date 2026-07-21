### Title
`L1Provider::start_block` Missing `CatchingUp` State Guard Allows Already-Committed L1 Handler Transactions to Be Re-Proposed - (`File: crates/apollo_l1_provider/src/l1_provider.rs`)

### Summary

`L1Provider::start_block` only guards against `Uninitialized` state. It does not guard against `CatchingUp` state. When the provider is catching up and the batcher calls `start_block` with a height that matches `current_height`, the provider silently transitions from `CatchingUp` to `Propose` or `Validate` with an incomplete `TransactionManager` — specifically, transactions from backlogged committed blocks are still marked `Pending`. Those transactions are then returned by `get_txs` and fed to the blockifier for re-execution, producing a wrong L1 message result, wrong state root, and wrong block hash.

### Finding Description

`ProviderState` documentation explicitly states that `start_block` is the transition from `Pending` to `Propose`/`Validate`:

```
/// Provider is not ready for proposing or validating. Use start_block to transition to Propose
/// or Validate.
Pending,
``` [1](#0-0) 

However, the implementation only rejects `Uninitialized`:

```rust
pub fn start_block(&mut self, height: BlockNumber, state: SessionState) -> L1ProviderResult<()> {
    if self.state.is_uninitialized() {
        return Err(L1ProviderError::Uninitialized);
    }
    self.check_height_with_error(height)?;
    self.state = state.into();          // ← transitions CatchingUp → Propose/Validate
    self.tx_manager.start_block();
    Ok(())
}
``` [2](#0-1) 

The `CatchingUp` state is entered when `commit_block` receives a height that does not match `current_height`. During catchup, `accept_commit_while_catching_up` processes incoming `commit_block` calls sequentially. When `new_height == current_height`, it calls `apply_commit_block` (which increments `current_height`) but only transitions to `Pending` if `is_caught_up` returns true. If the backlog is not yet exhausted, the state remains `CatchingUp` while `current_height` has already advanced. [3](#0-2) 

The batcher calls `start_block` unconditionally as part of `propose_block` and `validate_block`, ignoring errors:

```rust
// Ignore errors. If start_block fails, then subsequent calls to l1 provider will fail on
// out of session and l1 provider will restart and bootstrap again.
let _ = self.l1_provider_client
    .start_block(SessionState::Propose, propose_block_input.block_info.block_number)
    .await
    .inspect_err(|err| { ... });
``` [4](#0-3) 

**Exact trigger sequence:**

1. Provider is in `CatchingUp` state; `current_height = N`.
2. Batcher calls `commit_block(N, ...)` → `accept_commit_while_catching_up` applies it, `current_height` becomes `N+1`. Backlog still has entries for heights `> N+1`, so `is_caught_up` returns false; state stays `CatchingUp`.
3. Batcher calls `start_block(N+1, Propose)`. `check_height_with_error(N+1)` passes. No `CatchingUp` guard exists. State transitions to `Propose`. `tx_manager.start_block()` (= `rollback_staging()`) is called — it does **not** apply the backlog.
4. Batcher calls `get_txs(n, N+1)`. Provider is now in `Propose` state. `tx_manager.get_txs` scans `proposable_index` and returns transactions whose committed blocks are still in the backlog — those transactions are still `Pending` in the `TransactionManager`. [5](#0-4) [6](#0-5) 

### Impact Explanation

The L1 provider returns already-committed L1 handler transactions as valid for inclusion in block `N+1`. The batcher passes them to the blockifier, which re-executes them. This produces:

- A wrong L1 message result (duplicate L1→L2 message execution).
- A wrong state root (storage values written by the re-executed L1 handler differ from the correct post-state).
- A wrong block hash derived from that state root.

This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing** (the L1 provider is the admission gate for L1 handler transactions), and potentially **Critical — Wrong state, receipt, event, L1 message from blockifier/syscall/execution logic for accepted input**.

### Likelihood Explanation

The `CatchingUp` state is entered whenever the batcher sends a `commit_block` with a height that skips the provider's `current_height` — a normal occurrence at startup or after a crash. The batcher always calls `start_block` for every new block proposal/validation round without checking the provider's state. The height alignment condition (`current_height` matching the batcher's next block) is satisfied as soon as the provider processes one sequential `commit_block` during catchup. This is a routine operational path, not an edge case.

### Recommendation

Add a `CatchingUp` guard to `start_block`, mirroring the pattern used in `get_txs` and `validate`:

```rust
pub fn start_block(&mut self, height: BlockNumber, state: SessionState) -> L1ProviderResult<()> {
    if self.state.is_uninitialized() {
        return Err(L1ProviderError::Uninitialized);
    }
    if self.state.is_catching_up() {
        return Err(L1ProviderError::CatchingUp);  // add this variant
    }
    self.check_height_with_error(height)?;
    self.state = state.into();
    self.tx_manager.start_block();
    Ok(())
}
``` [2](#0-1) 

Alternatively, assert `self.state == ProviderState::Pending` at the top of `start_block`, consistent with the documented contract.

### Proof of Concept

```rust
#[tokio::test]
fn start_block_during_catchup_exposes_committed_txs() {
    // Provider is catching up; backlog has block N+1 with tx_hash!(2) committed.
    let catchupper = make_catchupper!(backlog: [1 => [2]]);
    let mut provider = L1ProviderContentBuilder::new()
        .with_catchupper(catchupper)
        .with_txs([l1_handler(2)])          // tx 2 is still Pending in tx_manager
        .with_height(BlockNumber(0))
        .with_state(ProviderState::Uninitialized)
        .build_into_l1_provider();

    provider.initialize(BlockNumber(0), vec![]).await.unwrap();
    provider.state = ProviderState::CatchingUp;
    provider.catchupper.start_l2_sync(BlockNumber(0), BlockNumber(1));

    // Batcher commits block 0 → current_height becomes 1, backlog not yet applied.
    commit_block_no_rejected(&mut provider, &[], BlockNumber(0));
    assert_eq!(provider.state, ProviderState::CatchingUp); // still catching up
    assert_eq!(provider.current_height, BlockNumber(1));

    // Batcher calls start_block for height 1 — should fail but doesn't.
    provider.start_block(BlockNumber(1), ProposeSession).unwrap();
    assert_eq!(provider.state, ProviderState::Propose); // ← wrong: bypassed CatchingUp

    // tx 2 was committed in the backlog but is still Pending → returned here.
    let txs = provider.get_txs(10, BlockNumber(1)).unwrap();
    assert!(!txs.is_empty()); // ← already-committed tx re-proposed
}
``` [7](#0-6) [2](#0-1)

### Citations

**File:** crates/apollo_l1_provider_types/src/lib.rs (L409-417)
```rust
    /// Provider is not ready for proposing or validating. Use start_block to transition to Propose
    /// or Validate.
    Pending,
    /// Provider is ready for proposing. Use get_txs to get what you need for a new proposal. Use
    /// commit_block to finish and return to Pending.
    Propose,
    /// Provider is ready for validating. Use validate to validate a transaction. Use commit_block
    /// to finish and return to Pending.
    Validate,
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L202-216)
```rust
    pub fn start_block(
        &mut self,
        height: BlockNumber,
        state: SessionState,
    ) -> L1ProviderResult<()> {
        if self.state.is_uninitialized() {
            return Err(L1ProviderError::Uninitialized);
        }

        self.check_height_with_error(height)?;
        info!("Starting block at height: {height}");
        self.state = state.into();
        self.tx_manager.start_block();
        Ok(())
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

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L383-474)
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
```

**File:** crates/apollo_batcher/src/batcher.rs (L302-314)
```rust
        // Ignore errors. If start_block fails, then subsequent calls to l1 provider will fail on
        // out of session and l1 provider will restart and bootstrap again.
        let _ = self
            .l1_provider_client
            .start_block(SessionState::Propose, propose_block_input.block_info.block_number)
            .await
            .inspect_err(|err| {
                error!(
                    "L1 provider is not ready to start proposing block {}: {}. ",
                    propose_block_input.block_info.block_number, err
                );
                BATCHER_L1_PROVIDER_ERRORS.increment(1);
            });
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L67-69)
```rust
    pub fn start_block(&mut self) {
        self.rollback_staging();
    }
```
