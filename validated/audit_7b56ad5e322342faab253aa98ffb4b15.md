### Title
Send-on-closed-channel panic in `BidPool` due to unsynchronized `stop()` vs. concurrent `AddBid`/`HandleBid` from RPC/P2P bid submission - (File: `kaiax/auction/impl/bid_pool.go`)

### Summary
`BidPool.stop()` closes `bp.newBidCh` and `bp.bidMsgCh` without first quiescing in-flight `AddBid`/`HandleBid` calls that are concurrently about to send on those same channels, mirroring the netfilter flowtable bug class: cleanup (`stop`/free) races with pending work items that are not flushed/fenced before the tearing-down step, letting a stale operation touch already-freed/closed state.

### Finding Description
`BidPool.stop()` performs, without any mutex serializing it against `AddBid`: [1](#0-0) 

`AddBid` (reachable via the public `auction_submitBid` RPC through `AuctionAPI.SubmitBid`) first checks the atomic `running` flag, then does unrelated work (`validateBid`, `insertBid`), and only afterwards sends on `bp.newBidCh`: [2](#0-1) 

`HandleBid` similarly checks `running` and then sends on `bp.bidMsgCh` outside of any lock that `stop()` also acquires: [3](#0-2) 

There is a race window: a caller can observe `running == 1` and pass the `AddBid`/`HandleBid` gate, then—while it is doing signature validation/bid insertion—`AuctionModule.Stop()` → `BidPool.stop()` runs concurrently, sets `running = 0`, and (unconditionally, guarded only by the one-shot `stopped` CAS) closes `bp.newBidCh`/`bp.bidMsgCh`. The already-in-flight goroutine then executes `bp.newBidCh <- bid` or `bp.bidMsgCh <- bid` on a channel that has just been closed, which is not a receive-check (`ok`) but a **send**, and sending on a closed channel unconditionally panics in Go. This is the exact bug class described in the report: cleanup work (`stop`) proceeds and frees/closes resources while a "pending" operation admitted earlier is still outstanding and later touches the torn-down resource, causing a crash instead of a graceful drop.

`AuctionModule.Stop()` is the caller of `bp.stop()`: [4](#0-3) 

`AuctionAPI.SubmitBid` is the externally reachable entry point that ultimately calls `AddBid`: [5](#0-4) 

### Impact Explanation
A panic inside `AddBid`/`HandleBid` triggered from an RPC handler or bid-message handler goroutine crashes the node process (Go panics that are not recovered terminate the program, and these handlers do not have a `recover()`), producing a denial of service on the consensus node running the auction module. Because `Stop()` can be invoked as part of normal module lifecycle transitions (e.g., feature toggling, config reload, node shutdown/restart sequences) while bid traffic keeps arriving concurrently, an unprivileged auction bidder submitting bids via `auction_submitBid` at the moment of a `Stop()`/lifecycle transition can trigger this crash, taking the block-building node offline.

### Likelihood Explanation
The race window is narrow (between the `running` check and the channel send, during `validateBid`/`insertBid`/`getBidTxGasLimit`), but a bidder can maximize the chance of hitting it by submitting a stream of bids continuously; any bid in flight at the exact moment `stop()` executes `close()` will panic. Because `stop()`'s `clearBidPool()` and channel-close are not covered by `bidMu`/any lock shared with the send path, there is no memory barrier preventing this interleaving, so with enough concurrent bid volume during a `Stop()` event, the panic is reliably reproducible.

### Recommendation
Do not close `bp.newBidCh`/`bp.bidMsgCh` while producers may still be sending. Instead, use a `done`/`quit` channel checked in a `select` at every send site (`AddBid`, `HandleBid`) instead of directly closing the channel that producers write to, or protect the send with the same lock/refcount that `stop()` uses to drain outstanding senders before closing (analogous to the flowtable fix: flush pending admitted work first, then tear down). Concretely, change `bp.newBidCh <- bid` / `bp.bidMsgCh <- bid` to a `select` with a `stopCh`/`quit` case so `stop()` only needs to close `quit`, not the channel that producers write into, eliminating the send-on-closed-channel panic.

### Proof of Concept
1. Start the node with the auction module enabled and `bidPool.start()` invoked (module `Start()`).
2. From an external client, continuously call `auction_submitBid` with valid bid payloads in a tight loop/multiple goroutines so that `AddBid` is frequently mid-flight between the `running` check and `bp.newBidCh <- bid`.
3. Concurrently trigger `AuctionModule.Stop()` (e.g., via the same lifecycle event that legitimately calls it, such as node shutdown/restart or module toggling in the test harness) so `BidPool.stop()` executes `close(bp.newBidCh)`/`close(bp.bidMsgCh)`.
4. Observe that a bid submission in flight at that moment executes `bp.newBidCh <- bid` (or `bp.bidMsgCh <- bid` from `HandleBid`) on the now-closed channel, causing `panic: send on closed channel`, crashing the process — reproducible deterministically with a `go test -race` style scenario interleaving `AddBid`/`HandleBid` calls with `stop()`.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L127-138)
```go
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

**File:** kaiax/auction/impl/bid_pool.go (L252-274)
```go
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

**File:** kaiax/auction/impl/bid_pool.go (L421-437)
```go
func (bp *BidPool) HandleBid(peerID string, bid *auction.Bid) {
	if atomic.LoadUint32(&bp.running) == 0 || bid == nil {
		return
	}

	// Check rate limit for this peer
	if !bp.checkRateLimit(peerID) {
		logger.Trace("Rate limit exceeded for peer", "peerID", peerID)
		return
	}

	select {
	case bp.bidMsgCh <- bid:
	default:
		logger.Trace("Bid queue is full, dropping bid", "peerID", peerID)
	}
}
```

**File:** kaiax/auction/impl/init.go (L118-122)
```go
func (a *AuctionModule) Stop() {
	logger.Info("AuctionModule stopped")
	// Clear the existing auction pool.
	a.bidPool.stop()
}
```

**File:** kaiax/auction/impl/api.go (L118-142)
```go
func (api *AuctionAPI) SubmitBid(ctx context.Context, bidInput BidInput) RPCOutput {
	numBidRequestCounter.Inc(1)
	if api.a.IsDisabled() {
		return makeRPCOutput(EMPTY_HASH, auction.ErrAuctionDisabled)
	}

	//  1. directly send target transaction
	targetTx, errTxDecode := toTx(bidInput.TargetTxRaw)
	if errTxDecode != nil {
		return makeRPCOutput(EMPTY_HASH, errTxDecode)
	}
	if targetTx.Hash() != bidInput.TargetTxHash {
		return makeRPCOutput(EMPTY_HASH, auction.ErrInvalidTargetTxHash)
	}
	errTargetTxSend := api.a.Backend.SendTx(ctx, targetTx)
	// ignore known transaction related errors against target tx validation
	if errTargetTxSend != nil && !(strings.HasPrefix(errTargetTxSend.Error(), "known transaction:") || errors.Is(errTargetTxSend, gasless_impl.ErrUnableToAddKnownBundleTx)) {
		return makeRPCOutput(EMPTY_HASH, errTargetTxSend)
	}

	// 2. add bid
	bid := ToBid(bidInput)
	bidHash, errValidateBid := api.a.bidPool.AddBid(bid)
	return makeRPCOutput(bidHash, errValidateBid)
}
```
