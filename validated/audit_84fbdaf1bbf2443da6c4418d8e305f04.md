### Title
Rejected L1 Handler Transactions Silently Dropped During Catchup, Corrupting L1 Provider Internal Accounting - (File: `crates/apollo_l1_provider/src/l1_provider.rs`)

### Summary

When the `L1Provider` is in `CatchingUp` state, the `rejected_txs` argument is silently discarded in `commit_block`. As a result, every L1 handler transaction attempted in a block — including those that failed execution — is permanently marked `Committed` in the provider's internal `TransactionManager`. This corrupts the provider's accounting: rejected L1 handler transactions that should remain `Pending` (and be re-proposed) are instead treated as successfully consumed, causing them to be permanently suppressed. A validator node that went through catchup will subsequently return `AlreadyIncludedOnL2` for any re-proposed rejected transaction, causing it to reject a valid block.

### Finding Description

**Root cause — `rejected_txs` dropped at the catchup branch:**

In `L1Provider::commit_block`, when the provider is in `CatchingUp` state, the function immediately delegates to `accept_commit_while_catching_up` and passes only `committed_txs` (which equals the full `consumed_l1_handler_tx_hashes` set, including rejected ones). The `rejected_txs` argument is never forwarded:

```rust
if self.state.is_catching_up() {
    return self.accept_commit_while_catching_up(committed_txs, height);
}
``` [1](#0-0) 

`accept_commit_while_catching_up` only accepts `committed_txs` and `new_height`; there is no parameter for `rejected_txs`. When the height matches, it calls:

```rust
Equal => self.apply_commit_block(committed_txs, Default::default()),
``` [2](#0-1) 

A developer TODO comment at that exact line acknowledges the gap: `// TODO(guyn): check what about rejected txs here and in the backlog?`

When blocks are applied from the backlog after catchup completes, the same pattern repeats:

```rust
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
``` [3](#0-2) 

**What `apply_commit_block` does with an empty `rejected_txs`:**

```rust
fn apply_commit_block(
    &mut self,
    consumed_txs: IndexSet<TransactionHash>,
    rejected_txs: IndexSet<TransactionHash>,
) {
    let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
        consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
    self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
    ...
}
``` [4](#0-3) 

Because `rejected_txs` is empty, the partition puts every hash into `committed_txs` and nothing into `rejected_and_consumed`. `TransactionManager::commit_txs` then calls `mark_committed()` on every L1 handler transaction, including those that failed execution. [5](#0-4) 

**What the batcher actually sends:**

The batcher correctly computes `rejected_l1_handler_tx_hashes` as the intersection of `rejected_tx_hashes` and `consumed_l1_handler_tx_hashes`, then passes both sets to `l1_provider_client.commit_block`. The information is present at the call site but is discarded inside the provider when it is catching up. [6](#0-5) 

**The test that confirms all L1 handler txs (including failed ones) enter `consumed_l1_handler_tx_hashes`:**

```rust
// Verify that all L1 handler transaction's are included in the consumed l1 transactions.
assert_eq!(
    result_block_artifacts.execution_data.consumed_l1_handler_tx_hashes,
    l1_handler_txs.iter().map(|tx| tx.tx_hash()).collect::<IndexSet<_>>()
);
``` [7](#0-6) 

This confirms that a rejected L1 handler transaction hash is present in `consumed_l1_handler_tx_hashes` and will be passed as `committed_txs` to the catchup path, where it will be incorrectly marked `Committed`.

### Impact Explanation

After catchup, the `TransactionManager` records for rejected L1 handler transactions carry state `Committed` instead of `Rejected`. This has two concrete sequencer-level effects:

1. **Proposer suppresses re-proposal.** `get_txs` only returns transactions in `Pending` state. A transaction marked `Committed` is never returned, so the rejected L1 message is permanently dropped from future blocks.

2. **Validator rejects valid blocks.** If a proposer node that did *not* go through catchup correctly re-proposes the rejected transaction, a validator node that *did* go through catchup will call `validate(tx_hash)`, find the record in `Committed` state, and return `AlreadyIncludedOnL2`. The validator then fails the block, rejecting a valid proposal.

This matches the allowed impact: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

### Likelihood Explanation

The `CatchingUp` state is entered during normal operation whenever the batcher calls `commit_block` with a height that differs from the provider's `current_height` — for example, after a node restart, a crash recovery, or any transient height desynchronisation. Any block in that window that contained a rejected L1 handler transaction will trigger the incorrect accounting. The developer TODO comment at the exact affected line confirms the gap is known but unresolved.

### Recommendation

1. Add `rejected_txs: IndexSet<TransactionHash>` as a parameter to `accept_commit_while_catching_up`.
2. Store `rejected_txs` alongside `committed_txs` in the `CommitBlockBacklogEntry` struct.
3. Pass the stored `rejected_txs` to `apply_commit_block` when draining the backlog, instead of `Default::default()`.
4. Remove the TODO comment once the fix is in place.

### Proof of Concept

```
// State: L1Provider is in CatchingUp state at height N.
// An L1 handler transaction T was scraped and is Pending.

// Step 1: Batcher executes block N. T fails execution (rejected).
// BlockTransactionExecutionData:
//   consumed_l1_handler_tx_hashes = {T}   // all L1 handlers attempted
//   rejected_tx_hashes             = {T}   // T failed

// Step 2: Batcher calls commit_block on L1Provider:
//   committed_txs = {T}   (consumed_l1_handler_tx_hashes)
//   rejected_txs  = {T}   (intersection with rejected_tx_hashes)

// Step 3: Inside L1Provider::commit_block, CatchingUp branch:
//   accept_commit_while_catching_up({T}, N)
//   -- rejected_txs = {T} is DROPPED here --

// Step 4: apply_commit_block({T}, {}) is called.
//   partition: rejected_and_consumed = [], committed_txs = [T]
//   tx_manager.commit_txs([T], [])
//   --> T.mark_committed()   // BUG: should be mark_rejected()

// Step 5: Provider exits catchup. T is now Committed.
//   get_txs() will never return T again.
//   validate(T) returns AlreadyIncludedOnL2.

// Step 6: A correct proposer re-proposes T in block N+k.
//   Validator (post-catchup) calls validate(T) -> AlreadyIncludedOnL2.
//   Validator rejects the block. Valid block is lost.
```

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

**File:** crates/apollo_batcher/src/block_builder_test.rs (L1096-1100)
```rust
    // Verify that all L1 handler transaction's are included in the consumed l1 transactions.
    assert_eq!(
        result_block_artifacts.execution_data.consumed_l1_handler_tx_hashes,
        l1_handler_txs.iter().map(|tx| tx.tx_hash()).collect::<IndexSet<_>>()
    );
```
