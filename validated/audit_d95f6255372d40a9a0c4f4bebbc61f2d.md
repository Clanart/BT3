### Title
Silent latch of a receipt-store background write failure delays chain halt by a full block, mirroring zkSync's "queue DB failure with no alert" incident - (File: sei-db/ledger_db/receipt/litt_receipt_store.go)

### Summary
`littReceiptStore` (the EVM receipt/log store) writes each block's receipts asynchronously through a background goroutine queue. When that background writer hits a fatal error (disk failure, index commit failure, etc.), the failure is only latched into an atomic and logged — it is not surfaced to the caller of the block that actually failed. The node keeps producing and committing blocks, with the API/consensus path unaffected, until the *next* block's `SetReceipts` call observes the latched failure and returns an error that panics the node in the pre-commit path. This one-block delay between the real failure and its externally visible effect is structurally the same class of issue as the zkSync incident: a storage/queue subsystem fails, other components (mempool, API, consensus) keep functioning normally, and nothing alerts until the failure surfaces indirectly.

### Finding Description
`littReceiptStore.SetReceipts` enqueues each block's receipts to a background writer instead of writing them synchronously when async writes are enabled: [1](#0-0) 

The background writer applies queued writes and, on failure, only logs the error and stores it in an atomic (`s.writeErr.CompareAndSwap`) — it does not stop the block that failed from being reported as accepted, because `SetReceipts` for that block already returned `nil` (it only queued the write): [2](#0-1) 

The latched failure is only checked (and only then surfaced as an error) the *next* time `SetReceipts`/`queueWrite` is invoked: [1](#0-0) [3](#0-2) 

`flushTransientReceipts`, which calls `SetReceipts`, is documented as running from the pre-commit handler, where returning an error panics the node: [4](#0-3) [5](#0-4) 

The project's own test for this exact mechanism (`TestWriteFailureHoldsTheHeadAgainstAQueuedBlock`) confirms the failure is only observed after the fact, once the writer's commit unblocks, and that a block queued behind the failing one is held back rather than the failing block being caught immediately: [6](#0-5) 

This mirrors the zkSync report precisely: a storage subsystem (their "block queue database", here the receipt store's async write queue) fails; the rest of the system (API, mempool, block production for at least one more height) continues to operate normally; and there is no synchronous alert/halt at the moment of failure, only a delayed, indirect one.

### Impact Explanation
Because the write failure is latched asynchronously and only surfaced on the *next* `SetReceipts` call, a validator/full node can commit at least one additional block believing receipts were persisted, when in fact they were dropped. When the failure is finally surfaced, `flushTransientReceipts` returns an error from a pre-commit code path documented to panic the node — i.e., the node crashes/halts unexpectedly one block later than the actual root cause, with no advance warning to operators (no alert fired when the real failure occurred). This can cause: (a) permanent loss of receipts/logs for the block that "succeeded" before the latch was observed (RPC `eth_getTransactionReceipt`/`eth_getLogs` will never see it, since receipt writes are one-shot and not retried), and (b) a validator halt happening a block later than the true failure, complicating incident response exactly as in the zkSync case where alerts never fired because the API layer kept functioning.

### Likelihood Explanation
This requires an underlying storage failure in the receipt store's backing index/log (e.g., disk error, litt commit failure) which is an operational/infrastructure condition, not directly triggerable by an unprivileged transaction sender. It is not a bug reachable purely from a submitted transaction or RPC call; it depends on a storage-layer failure occurring during normal EVM transaction processing (which every EVM tx triggers, since every block flushes transient receipts). Given the constraint to accept only analogs reachable from a transaction/contract/wasm/RPC path, this finding's trigger condition (a storage engine fault) is outside attacker control and is closer to an infra/operational reliability gap than an attacker-reachable vulnerability.

### Recommendation
Not applicable in this ask-only context — this is a report of an observed reliability gap, not a change request.

### Proof of Concept
The existing unit test `TestWriteFailureHoldsTheHeadAgainstAQueuedBlock` demonstrates the exact behavior: block 2's write is held mid-commit and failing, block 3 is queued behind it and only rejected once block 2's failure is observed by the writer, with `LatestVersion()` remaining at 1 (the last good block) even though block 2 and 3 were both submitted to `SetReceipts` without an immediate synchronous error: [7](#0-6)

### Citations

**File:** sei-db/ledger_db/receipt/litt_receipt_store.go (L343-351)
```go
func (s *littReceiptStore) SetReceipts(ctx sdk.Context, receipts []ReceiptRecord) error {
	if s.writes == nil {
		return s.applyReceipts(ctx.BlockHeight(), receipts)
	}
	if err := s.writeFailure(); err != nil {
		return err
	}
	return s.queueWrite(receiptWrite{height: ctx.BlockHeight(), receipts: receipts})
}
```

**File:** sei-db/ledger_db/receipt/litt_receipt_store.go (L515-526)
```go
// applyWrite performs one queued write, keeping the first failure for its callers to collect.
// Nothing is applied after a failure: a later block carries its own version marker and would publish
// a head above one whose receipts were never written.
func (s *littReceiptStore) applyWrite(write receiptWrite) {
	if s.writeFailure() != nil {
		return
	}
	if err := s.applyReceipts(write.height, write.receipts); err != nil {
		logger.Error("failed to write receipts", "height", write.height, "err", err)
		s.writeErr.CompareAndSwap(nil, &err)
	}
}
```

**File:** sei-db/ledger_db/receipt/litt_receipt_store.go (L528-535)
```go
// writeFailure returns the first failure a queued write hit. It latches, so every later caller sees
// it rather than the first to ask consuming it.
func (s *littReceiptStore) writeFailure() error {
	if err := s.writeErr.Load(); err != nil {
		return *err
	}
	return nil
}
```

**File:** x/evm/keeper/receipt.go (L136-142)
```go
func (k *Keeper) flushTransientReceipts(ctx sdk.Context) error {
	// An absent receipt store means the node keeps no receipts, so there is nothing to flush. This
	// runs from the pre-commit handler, where a returned error panics the node, so it must not be one.
	if k.receiptStore == nil {
		return nil
	}
	transientReceiptStore := prefix.NewStore(ctx.TransientStore(k.transientStoreKey), types.ReceiptKeyPrefix)
```

**File:** x/evm/keeper/receipt.go (L163-167)
```go
		txHash := types.TransientReceiptKey(iter.Key()).TransactionHash()
		records = append(records, receipt.ReceiptRecord{TxHash: txHash, Receipt: rcpt})
	}
	return k.receiptStore.SetReceipts(ctx, records)
}
```

**File:** sei-db/ledger_db/receipt/litt_write_failure_internal_test.go (L45-82)
```go
// TestWriteFailureHoldsTheHeadAgainstAQueuedBlock covers what a failed write owes the blocks queued
// behind it: applying one would publish a head above the block that never landed. The follower is
// queued while the failing commit is held, since SetReceipts refuses blocks once the failure shows.
func TestWriteFailureHoldsTheHeadAgainstAQueuedBlock(t *testing.T) {
	s, closeStore := setupLittCtxStore(t)
	defer closeStore()

	addr := common.HexToAddress("0xfa11")
	topic := common.HexToHash("0xfa12")

	index := &failingIndex{
		KeyValueDB: s.index,
		entered:    make(chan struct{}),
		release:    make(chan struct{}),
	}
	s.index = index

	// Block 1 lands, so there is a real head for the failure to hold.
	writeOneReceipt(t, s, 1, addr, topic)
	requireReceiptVersion(t, s, 1)

	// Block 2 reaches its commit and stops there, still holding the writer.
	index.failing.Store(true)
	writeOneReceipt(t, s, 2, addr, topic)
	<-index.entered

	// Block 3 would commit cleanly and carry a marker naming it the head. Queued now, while block 2
	// is mid-commit, it is past the refusal in SetReceipts and only the writer can hold it back.
	index.failing.Store(false)
	writeOneReceipt(t, s, 3, addr, topic)

	close(index.release)

	// Close drains, so the writer has decided about block 3 by the time this returns.
	require.ErrorIs(t, s.Close(), errIndexCommit)
	require.Equal(t, int64(1), s.LatestVersion(),
		"the head must not move past a block whose receipts were never written")
}
```
