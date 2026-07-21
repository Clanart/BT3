### Title
Rejected L1 Handler Transactions Permanently Marked as Committed During Catch-Up, Silently Dropping Valid L1 Messages - (File: crates/apollo_l1_provider/src/l1_provider.rs)

### Summary
When the L1 provider enters `CatchingUp` state, the `rejected_txs` argument is silently dropped from every `commit_block` call. The catch-up path (`accept_commit_while_catching_up`) accepts only `committed_txs` and `new_height`; it never stores nor forwards `rejected_txs`. When the backlog is later replayed, every consumed L1 handler transaction — including those that failed execution on L2 — is permanently marked `Committed` instead of `Rejected`. Those transactions are then invisible to future proposers and validators, permanently excluding valid L1 messages from L2 sequencing.

### Finding Description

**Root cause — `rejected_txs` is not a parameter of `accept_commit_while_catching_up`**

`L1Provider::commit_block` receives both `committed_txs` (all consumed L1 handler hashes) and `rejected_txs` (the subset that failed execution). When the provider is in `CatchingUp` state it immediately re-routes to:

```rust
// crates/apollo_l1_provider/src/l1_provider.rs  line 313-316
if self.state.is_catching_up() {
    return self.accept_commit_while_catching_up(committed_txs, height);
}
```

`rejected_txs` is never forwarded. The function signature confirms it:

```rust
// line 383-387
fn accept_commit_while_catching_up(
    &mut self,
    committed_txs: IndexSet<TransactionHash>,
    new_height: BlockNumber,
) -> L1ProviderResult<()> {
```

**Backlog stores only `committed_txs`**

When `new_height > current_height` the block is queued:

```rust
// line 429-434
Greater => {
    self.catchupper.add_commit_block_to_backlog(committed_txs, new_height);
    return Ok(());
}
```

`rejected_txs` is absent from the backlog entry.

**Backlog replay uses `Default::default()` for `rejected_txs`**

After catch-up completes every backlogged block is applied with an empty rejection set:

```rust
// lines 463-465
for committed_block in backlog {
    self.apply_commit_block(committed_block.committed_txs, Default::default());
}
```

**`apply_commit_block` partitions on `rejected_txs`**

```rust
// lines 371-373
let (rejected_and_consumed, committed_txs): (Vec<_>, Vec<_>) =
    consumed_txs.iter().copied().partition(|tx| rejected_txs.contains(tx));
self.tx_manager.commit_txs(&committed_txs, &rejected_and_consumed);
```

With an empty `rejected_txs`, the partition always puts every consumed hash into `committed_txs`. `commit_txs` then calls `mark_committed()` on all of them, including those that failed execution.

**The same defect exists in the `Equal` arm** (the block that matches the current height during catch-up):

```rust
// lines 426-427
// TODO(guyn): check what about rejected txs here and in the backlog?
Equal => self.apply_commit_block(committed_txs, Default::default()),
```

The TODO comment confirms the developers identified this gap but left it unresolved.

**Batcher-side filter that creates the rejected set**

In `commit_proposal_and_block` the batcher correctly computes the rejected L1 handler subset before calling the provider:

```rust
// crates/apollo_batcher/src/batcher.rs  lines 885-893
let rejected_l1_handler_tx_hashes = rejected_tx_hashes
    .iter()
    .copied()
    .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
    .collect();

self.l1_provider_client
    .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
    .await;
```

The correct data is sent; it is discarded inside the provider the moment catch-up is active.

### Impact Explanation

An L1 handler transaction that fails execution on L2 (e.g. due to a contract error) is still considered *consumed* on L1 — the L1 contract will never re-emit it. The L1 provider's `Rejected` state exists precisely to allow the sequencer to re-propose such transactions in a later block. Once the transaction is instead marked `Committed`:

- `validate()` returns `InvalidValidationStatus::AlreadyIncludedOnL2` for it, causing any validator that receives a proposal containing it to reject the block.
- `get_txs()` never returns it again because it is no longer in the `proposable_index`.
- The L1 message is permanently unexecutable on L2 while the L1 contract considers it consumed.

This matches the **High** impact category: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing"* — specifically, valid L1 handler transactions that should be re-sequenced are permanently excluded.

### Likelihood Explanation

`CatchingUp` state is entered whenever `commit_block` arrives with a height that does not match `current_height`. This occurs routinely on:

- Provider restart (provider height lags behind batcher height).
- Any transient height mismatch between batcher and provider.

No privileged access is required; the condition arises from normal operational events. Any block that contains a failing L1 handler transaction while the provider is catching up will silently corrupt the provider's records.

### Recommendation

Pass `rejected_txs` through the entire catch-up path:

1. Add `rejected_txs: IndexSet<TransactionHash>` as a parameter to `accept_commit_while_catching_up`.
2. Store `rejected_txs` alongside `committed_txs` in the backlog entry struct.
3. In the `Equal` arm and in the backlog replay loop, pass the stored `rejected_txs` to `apply_commit_block` instead of `Default::default()`.

### Proof of Concept

```
1. L1 provider is at height N (Pending state).
   tx_A is a pending L1 handler transaction.

2. Batcher executes block N+1 in which tx_A fails execution.
   Batcher calls:
     commit_block(consumed=[tx_A], rejected=[tx_A], height=N+1)
   But provider's current_height is N, so height check fails.
   Provider enters CatchingUp state targeting height N+1.
   accept_commit_while_catching_up([tx_A], N+1) is called — rejected=[tx_A] is dropped.

3. Batcher executes block N+2 and calls:
     commit_block(consumed=[], rejected=[], height=N+2)
   Provider is still catching up; N+2 > current_height → added to backlog.

4. L2 state sync delivers height N+1 to the catchupper.
   catchupper.is_caught_up(N+1) → true.
   Backlog is replayed:
     apply_commit_block([], Default::default())   ← backlog entry for N+2 (empty, harmless)
   Equal arm for N+1:
     apply_commit_block([tx_A], Default::default())
   partition: rejected_txs is empty → tx_A goes to committed_txs.
   commit_txs([tx_A], []) → tx_A.mark_committed()

5. Provider transitions to Pending at height N+2.
   tx_A is now TransactionState::Committed.

6. Next block proposal: get_txs() never returns tx_A (not in proposable_index).
   Next block validation: validate(tx_A) → AlreadyIncludedOnL2.
   tx_A is permanently lost; the L1 message will never execute on L2.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L429-434)
```rust
            Greater => {
                self.catchupper.add_commit_block_to_backlog(committed_txs, new_height);
                // No need to check the backlog or catchup completion, since those are only
                // applicable if we just increased the provider's height, like in the `Equal` case.
                return Ok(());
            }
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L463-465)
```rust
            for committed_block in backlog {
                self.apply_commit_block(committed_block.committed_txs, Default::default());
            }
```

**File:** crates/apollo_batcher/src/batcher.rs (L885-893)
```rust
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_provider_result = self
            .l1_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L147-163)
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
```
