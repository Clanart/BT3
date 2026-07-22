The code confirms the described bug. Let me trace through the exact path:

**`commit_block` drops `rejected_txs` when routing to CatchingUp:** [1](#0-0) 

`accept_commit_while_catching_up` only accepts `committed_txs` — the `rejected_txs` parameter is silently discarded at the call site.

**`CommitBlockBacklog` has no `rejected_txs` field:** [2](#0-1) 

**Backlog application uses `Default::default()` for rejected set:** [3](#0-2) 

**The `Equal` branch has the same problem, with a TODO acknowledging it:** [4](#0-3) 

**`apply_commit_block` partitions consumed txs into committed vs rejected using the rejected set:** [5](#0-4) 

With an empty `rejected_txs`, every tx in `consumed_txs` (including those that were actually rejected) is passed to `commit_txs` as committed.

**`mark_committed` sets `TransactionState::Committed` and sets `committed = true`:** [6](#0-5) 

**`is_validatable()` returns `false` for `Committed` state, and `is_proposable()` only returns `true` for `Pending`:** [7](#0-6) 

**The batcher correctly sends both committed and rejected L1 handler hashes:** [8](#0-7) 

---

### Title
Rejected L1 Handler Transactions Permanently Marked as Committed During CatchingUp Backlog Drain — (`crates/apollo_l1_provider/src/l1_provider.rs`)

### Summary
`accept_commit_while_catching_up` discards the `rejected_txs` argument before it is ever stored or used. Both the `Equal` branch and the backlog drain call `apply_commit_block(..., Default::default())`, so every L1 handler transaction in `consumed_txs` — including those the batcher explicitly marked as rejected — is committed to `TransactionState::Committed`. The transaction is then permanently ineligible for re-proposal or re-validation.

### Finding Description
When the L1 provider is in `CatchingUp` state, `commit_block` routes to `accept_commit_while_catching_up(committed_txs, height)`, dropping `rejected_txs` entirely. The `CommitBlockBacklog` struct stores only `committed_txs`; there is no field for rejected hashes. When the backlog is drained, each entry is applied via `apply_commit_block(committed_block.committed_txs, Default::default())`. Inside `apply_commit_block`, the partition `consumed_txs.partition(|tx| rejected_txs.contains(tx))` always puts every tx into the committed bucket because `rejected_txs` is empty. `tx_manager.commit_txs` then calls `mark_committed()` on each, setting `state = Committed` and `committed = true`. The `mark_committed` guard asserts `!self.committed`, so a second commit attempt would panic, but the first call silently overwrites what should have been `Rejected`. The developer acknowledged the gap with a `TODO` comment at the `Equal` branch.

### Impact Explanation
A rejected L1 handler transaction represents an L1→L2 message that failed execution and must be retried. After the incorrect `mark_committed` call, `is_proposable()` returns `false` (only `Pending` is proposable) and `is_validatable()` returns `false` (Committed is excluded). The transaction is permanently invisible to both the proposer and validator paths. The L1 message is silently dropped by the sequencer even though the L1 contract still holds it, causing a permanent divergence between L1 message state and L2 execution state.

### Likelihood Explanation
The `CatchingUp` state is entered on every node restart where the provider's height lags the batcher's height — a routine operational event. Any block committed during that window that contains a rejected L1 handler transaction triggers the bug. No adversarial action is required; normal sequencer operation is sufficient.

### Recommendation
1. Add a `rejected_txs: IndexSet<TransactionHash>` field to `CommitBlockBacklog`.
2. Update `add_commit_block_to_backlog` to accept and store the rejected set.
3. Pass the stored rejected set when draining the backlog: `apply_commit_block(committed_block.committed_txs, committed_block.rejected_txs)`.
4. Fix the `Equal` branch in `accept_commit_while_catching_up` to forward the `rejected_txs` argument instead of `Default::default()`.
5. Update `accept_commit_while_catching_up`'s signature to accept `rejected_txs`.

### Proof of Concept
```rust
// Pseudocode unit test (synchronous, no async needed)
let mut provider = /* build provider at height 5, CatchingUp toward height 6 */;
let rejected_tx = tx_hash!(42);

// Batcher sends a commit_block for height 7 (future → goes to backlog)
// consumed_txs includes rejected_tx; rejected_txs also includes rejected_tx
provider.commit_block(
    [rejected_tx].into(),   // consumed_txs
    [rejected_tx].into(),   // rejected_txs  ← dropped by accept_commit_while_catching_up
    BlockNumber(7),
).ok(); // backlogged

// Sync catches up: provider processes height 5 and 6, triggering backlog drain
provider.commit_block([].into(), [].into(), BlockNumber(5)).unwrap();
provider.commit_block([].into(), [].into(), BlockNumber(6)).unwrap();
// Backlog drained with Default::default() rejected set

// Assert: rejected_tx is now Committed, not Rejected
let record = provider.tx_manager.records.get(&rejected_tx).unwrap();
assert_eq!(record.state, TransactionState::Rejected); // FAILS: actual is Committed
```

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L313-316)
```rust
        if self.state.is_catching_up() {
            // Once catchup completes it will transition to Pending state by itself.
            return self.accept_commit_while_catching_up(committed_txs, height);
        }
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L371-373)
```rust
        let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
            consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
        self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L426-427)
```rust
            // TODO(guyn): check what about rejected txs here and in the backlog?
            Equal => self.apply_commit_block(committed_txs, Default::default()),
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L463-465)
```rust
            for committed_block in backlog {
                self.apply_commit_block(committed_block.committed_txs, Default::default());
            }
```

**File:** crates/apollo_l1_provider/src/catchupper.rs (L184-188)
```rust
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CommitBlockBacklog {
    pub height: BlockNumber,
    pub committed_txs: IndexSet<TransactionHash>,
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

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L155-181)
```rust
    pub fn is_proposable(&self) -> bool {
        matches!(self.state, TransactionState::Pending)
    }

    pub fn is_committed(&self) -> bool {
        matches!(self.state, TransactionState::Committed)
    }

    /// Answers whether the transaction was fully cancelled on L2 (cancellation request timelock
    /// has expired).
    pub fn is_cancelled(&self) -> bool {
        matches!(self.state, TransactionState::CancelledOnL2)
    }

    pub fn is_consumed(&self) -> bool {
        matches!(self.state, TransactionState::Consumed)
    }

    /// Answers whether any node can include this transaction in a block. This is generally possible
    /// in all states in its lifecycle, except after it had already been added to block, or a short
    /// time after it's cancellation was requested on L1. In particular, this includes states
    /// like: a rejected transaction, a new timelocked transaction, a
    /// transaction whose cancellation was requested on L1 too recently (there will be a
    /// timelock for this).
    pub fn is_validatable(&self) -> bool {
        !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
    }
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
