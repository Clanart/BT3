### Title
`rejected_txs` Silently Dropped in `accept_commit_while_catching_up` Causes Rejected L1 Handler Transactions to Be Permanently Marked Committed — (`File: crates/apollo_l1_provider/src/l1_provider.rs`)

### Summary

During the `CatchingUp` state of the `L1Provider`, the `rejected_txs` parameter passed to `commit_block` is silently discarded before being forwarded to `accept_commit_while_catching_up`. As a result, every L1 handler transaction that was rejected (execution-failed) in any block processed during catch-up — whether applied immediately at the current height or stored in the backlog — is incorrectly promoted to the `Committed` state instead of remaining `Pending`/`Rejected`. Once committed, those transactions are permanently ineligible for re-proposal, so the corresponding L1 messages are never properly sequenced on L2.

### Finding Description

`commit_block` receives both `committed_txs` and `rejected_txs`:

```rust
pub fn commit_block(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    rejected_txs: IndexSet<TransactionHash>,
    height: BlockNumber,
) -> L1ProviderResult<()> {
    ...
    if self.state.is_catching_up() {
        return self.accept_commit_while_catching_up(committed_txs, height);
        //                                          ^^^^^^^^^^^^^^^^^^^^
        //                                          rejected_txs is never forwarded
    }
``` [1](#0-0) 

`accept_commit_while_catching_up` does not accept a `rejected_txs` parameter at all:

```rust
fn accept_commit_while_catching_up(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    new_height: BlockNumber,
) -> L1ProviderResult<()>
``` [2](#0-1) 

Inside that function, both the immediate-apply path (`Equal`) and the backlog-apply path use `Default::default()` (empty set) for `rejected_txs`. The code even contains an unresolved TODO acknowledging the gap:

```rust
// TODO(guyn): check what about rejected txs here and in the backlog?
Equal => self.apply_commit_block(committed_txs, Default::default()),
``` [3](#0-2) 

And when the backlog is drained:

```rust
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
``` [4](#0-3) 

`apply_commit_block` partitions `consumed_txs` into committed vs rejected using the `rejected_txs` set:

```rust
let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
    consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
``` [5](#0-4) 

Because `rejected_txs` is always empty in the catching-up path, every consumed L1 handler transaction — including those that actually failed execution — is passed to `commit_txs` as a committed transaction.

The batcher explicitly computes and sends the rejected subset:

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

That information is thrown away the moment `commit_block` routes to `accept_commit_while_catching_up`.

The `Catchupper` backlog struct stores only `committed_txs`, with no field for `rejected_txs`:

```rust
pub struct Catchupper {
    ...
    pub commit_block_backlog: Vec<CommitBlockBacklog>,
    ...
}
``` [7](#0-6) 

```rust
pub fn add_commit_block_to_backlog(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    height: BlockNumber,
) {
``` [8](#0-7) 

### Impact Explanation

The `TransactionManager` distinguishes committed from rejected transactions:

- **Committed** → `AlreadyIncludedOnL2` → permanently ineligible for re-proposal.
- **Rejected** → kept `Pending` → eligible for re-proposal in a future block.

When a rejected L1 handler transaction is incorrectly promoted to `Committed`, the L1 provider will return `ValidationStatus::Invalid(AlreadyIncludedOnL2)` for it in every subsequent block, silently dropping the L1 message. The corresponding L1→L2 message is never executed on L2, producing a wrong L1 message outcome.

This matches: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing** (the L1 provider is the admission layer for L1 handler transactions and permanently rejects transactions that should be re-sequenced).

### Likelihood Explanation

The `CatchingUp` state is entered at every node startup when the batcher is ahead of the L1 provider, and also after any crash/restart. During that window the batcher continues building blocks normally. Any block that contains an L1 handler transaction whose execution fails (a routine occurrence for malformed or reverted L1 messages) and that arrives while the provider is catching up will trigger the bug. No privileged access is required; the condition arises from normal protocol operation.

### Recommendation

1. Add `rejected_txs: IndexSet<TransactionHash>` to `accept_commit_while_catching_up` and forward it from `commit_block`.
2. Add `rejected_txs` to `CommitBlockBacklog` so the information survives until the backlog is drained.
3. Pass the stored `rejected_txs` to `apply_commit_block` in both the `Equal` branch and the backlog-drain loop.
4. Remove the `TODO(guyn)` comment once the fix is in place and add a regression test that commits a block with rejected L1 handler transactions while in `CatchingUp` state and verifies those transactions remain `Pending` after the backlog is applied.

### Proof of Concept

```
1. Start L1Provider at height 0 (Pending state).
2. Batcher calls commit_block(consumed=[tx_A, tx_B], rejected=[tx_A], height=5).
   → Provider height != 5, so it enters CatchingUp, returns error.
3. Batcher calls commit_block(consumed=[tx_C], rejected=[tx_C], height=6).
   → height > current_height (0), backlogged with committed_txs=[tx_C], rejected_txs DROPPED.
4. Sync task feeds heights 0–4 to the provider via commit_block.
   → At height 4 the Equal branch fires:
      apply_commit_block([tx_A, tx_B], Default::default())
      → tx_A incorrectly committed (should be Rejected/Pending).
5. is_caught_up() returns true; backlog is drained:
      apply_commit_block([tx_C], Default::default())
      → tx_C incorrectly committed (should be Rejected/Pending).
6. Provider transitions to Pending.
7. Next block proposal: get_txs() returns nothing for tx_A and tx_C.
   validate(tx_A) → AlreadyIncludedOnL2  (should be Validated).
   validate(tx_C) → AlreadyIncludedOnL2  (should be Validated).
   Both L1 messages are permanently lost.
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

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L371-373)
```rust
        let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
            consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
        self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L383-387)
```rust
    fn accept_commit_while_catching_up(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        new_height: BlockNumber,
    ) -> L1ProviderResult<()> {
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

**File:** crates/apollo_l1_provider/src/catchupper.rs (L22-31)
```rust
pub struct Catchupper {
    pub target_height: BlockNumber,
    pub sync_retry_interval: Duration,
    pub commit_block_backlog: Vec<CommitBlockBacklog>,
    pub l1_provider_client: SharedL1ProviderClient,
    pub sync_client: SharedStateSyncClient,
    // Keep track of sync task for health checks and logging status.
    pub sync_task_handle: SyncTaskHandle,
    pub n_sync_health_check_failures: Arc<AtomicU8>,
}
```

**File:** crates/apollo_l1_provider/src/catchupper.rs (L65-70)
```rust
    pub fn add_commit_block_to_backlog(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        height: BlockNumber,
    ) {
        assert!(
```
