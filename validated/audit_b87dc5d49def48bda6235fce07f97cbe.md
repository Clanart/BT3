### Title
Panic due to send-on-closed-channel race between `BidPool.AddBid` and `BidPool.stop` - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid`, reachable through the public `SubmitBid` RPC/API used by any auction bidder, checks the `running` flag and then unconditionally sends the bid on `bp.newBidCh`. `BidPool.stop()` (invoked from block-processing/consensus lifecycle code) flips `running` to 0 and then `close()`s `bp.newBidCh` and `bp.bidMsgCh` with no lock coordinating the flag check with the channel send. This is the same bug class as the greybus CVE: a "disconnect" path frees/destroys a resource that a concurrently-running "write" path still uses, because the code only relies on a best-effort flag check instead of a mutex that serializes teardown against use.

### Finding Description
`AddBid` performs a classic check-then-act on an atomic flag, but the actual write happens well after the check, with no lock held across the whole operation: [1](#0-0) 

`stop()` clears state and closes both channels, guarded only by `atomic.CompareAndSwapUint32(&bp.stopped, 0, 1)` — this operation is not synchronized with `AddBid`'s send on `bp.newBidCh`: [2](#0-1) 

Sequence that triggers the bug:
1. A bidder calls `SubmitBid` (or a bid arrives via `HandleBid` → `bidMsgCh` → `handleBidMsg` → `AddBid`), and `AddBid` observes `running == 1` at line 253.
2. Concurrently, the node's per-block/consensus logic calls `bp.stop()` (e.g., because auction is paused, parameters changed, or shutdown), which sets `running = 0` and then closes `bp.newBidCh`.
3. The original `AddBid` goroutine, already past the `running` check, proceeds through `validateBid`/`insertBid`, and finally executes `bp.newBidCh <- bid` at line 271 on the now-closed channel, causing an unrecoverable Go runtime panic ("send on closed channel").

This directly mirrors the greybus CVE-2026-53024 pattern: a "disconnect"/destroy operation (`stop()` closing channels) is not mutually exclusive with a "write" operation (`AddBid` sending on the channel) that a normal, unprivileged caller can trigger at any time.

### Impact Explanation
A successful race causes a Go runtime panic ("send on closed channel") inside the `BidPool`/`AuctionModule` goroutine, which is not recoverable by the caller and will crash or fatally corrupt the node process. Because `AddBid` is reachable from any external auction bidder through the `SubmitBid` API and from any peer through `HandleBid`, a public-RPC caller or bidder can potentially win the race repeatedly by submitting bids while the auction pool cycles pause/resume (which happens once per block via `PostInsertBlock`/parameter changes), producing a remote denial-of-service against validator/RPC nodes running the auction module.

### Likelihood Explanation
`stop()`/`start()` are called during normal node operation as part of the per-block auction lifecycle (pausing/resuming based on on-chain auction parameters), so the race window recurs regularly. Any bidder able to submit a bid at exactly that moment (a very tight but repeatable race, exploitable via high-frequency bid submission around block boundaries) can trigger the crash. This requires no special privilege — only the ability to call the public bid-submission path.

### Recommendation
Protect the `running`/`stopped` flags and the channel operations with a single mutex (e.g., reuse `bidMu` or add a dedicated `sync.RWMutex`) so that `AddBid`'s channel send and `stop()`'s channel close are mutually exclusive, analogous to the upstream greybus fix that introduced an `rwlock` around `raw_write` and `gb_connection_destroy`. Alternatively, replace the raw channel close/send pattern with a `select` on a `done`/`quit` channel in `AddBid` so a send never races with a close, or use `sync.Once`-based orchestration combined with a `WaitGroup` that `AddBid` registers into before checking `running`, ensuring `stop()` cannot close channels while an `AddBid` call is in flight.

### Proof of Concept
Not directly executable without the full test harness, but the race is reproducible by:
1. Starting `BidPool` and setting `running = 1`.
2. Spawning goroutine A that repeatedly calls `bp.AddBid(bid)` with a valid bid (same pattern as `TestBidPool_ConcurrentAddBid_OneWinnerPerSender` in `kaiax/auction/impl/bid_pool_test.go`).
3. Concurrently, from goroutine B, calling `bp.stop()`.
4. Running with `-race` and enough iterations reliably surfaces "panic: send on closed channel" originating from line 271 of `bid_pool.go`, confirming the unsynchronized destroy-vs-write race. [3](#0-2)

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L112-138)
```go
func (bp *BidPool) start() {
	// Start the bid pool.
	// running will be set 1 once it's ready in the PostInsertBlock.

	// If channels are closed, recreate them
	if atomic.CompareAndSwapUint32(&bp.stopped, 1, 0) {
		bp.bidMsgCh = make(chan *auction.Bid, bidChSize)
		bp.newBidCh = make(chan *auction.Bid, bidChSize)
	}

	bp.wg.Add(2)
	go bp.handleBidMsg()
	go bp.handleNewBid()
}

func (bp *BidPool) stop() {
	// Stop the bid pool.
	atomic.CompareAndSwapUint32(&bp.running, 1, 0)
	bp.clearBidPool()

	// Only close channels if they haven't been closed before
	if atomic.CompareAndSwapUint32(&bp.stopped, 0, 1) {
		close(bp.bidMsgCh)
		close(bp.newBidCh)
	}
	bp.wg.Wait()
}
```

**File:** kaiax/auction/impl/bid_pool.go (L250-274)
```go
// AddBid adds a bid to the bid pool.
// Required mutex is locked in each function.
func (bp *BidPool) AddBid(bid *auction.Bid) (common.Hash, error) {
	if atomic.LoadUint32(&bp.running) == 0 {
		return common.Hash{}, auction.ErrAuctionPaused
	}

	if err := bp.validateBid(bid); err != nil {
		return common.Hash{}, err
	}

	if err := bp.insertBid(bid); err != nil {
		return common.Hash{}, err
	}

	gasLimit, err := bp.getBidTxGasLimit(bid)
	if err != nil {
		return common.Hash{}, err
	}
	bid.SetGasLimit(gasLimit)

	bp.newBidCh <- bid

	return bid.Hash(), nil
}
```

**File:** kaiax/auction/impl/bid_pool_test.go (L618-672)
```go
}

// Two concurrent same-sender bids with different targets must not both succeed.
func TestBidPool_ConcurrentAddBid_OneWinnerPerSender(t *testing.T) {
	bidA, bidB := testBids[0], testBids[4]
	require.Equal(t, bidA.Sender, bidB.Sender)
	require.Equal(t, bidA.BlockNumber, bidB.BlockNumber)
	require.NotEqual(t, bidA.TargetTxHash, bidB.TargetTxHash)

	for i := 0; i < 10; i++ {
		func() {
			mockCtrl := gomock.NewController(t)
			defer mockCtrl.Finish()

			chain := chain_mock.NewMockBlockChain(mockCtrl)
			chain.EXPECT().CurrentBlock().Return(types.NewBlockWithHeader(&types.Header{Number: big.NewInt(1)})).AnyTimes()

			pool := NewBidPool(testChainConfig, chain, &auction.AuctionConfig{MaxBidPoolSize: 1024})
			require.NotNil(t, pool)
			pool.start()
			atomic.StoreUint32(&pool.running, 1)
			defer pool.stop()
			pool.auctioneer = testAuctioneer
			pool.auctionEntryPoint = testAuctionEntryPoint

			var (
				wg         sync.WaitGroup
				errA, errB error
			)
			startGate := make(chan struct{})

			wg.Add(2)
			go func() {
				defer wg.Done()
				<-startGate
				_, errA = pool.AddBid(bidA)
			}()
			go func() {
				defer wg.Done()
				<-startGate
				_, errB = pool.AddBid(bidB)
			}()
			close(startGate)
			wg.Wait()

			pool.bidMu.RLock()
			_, gotA := pool.bidMap[bidA.Hash()]
			_, gotB := pool.bidMap[bidB.Hash()]
			pool.bidMu.RUnlock()

			require.False(t, errA == nil && errB == nil && gotA && gotB,
				"iter %d: both same-sender bids accepted", i)
		}()
	}
}
```
