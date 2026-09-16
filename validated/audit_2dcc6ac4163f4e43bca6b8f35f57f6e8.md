### Title
Send on closed bid channel causes node panic/DoS during AuctionModule shutdown - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid` and `BidPool.HandleBid` check the `running` flag and then unconditionally send to `bp.newBidCh` / `bp.bidMsgCh`, while `BidPool.stop()` closes these same channels. The running-flag check and the channel send/close are not atomic with respect to each other, creating a TOCTOU race analogous to CVE-2022-49136 (`hci_cmd_sync_queue` failing to reject queuing once `HCI_UNREGISTER` is set, causing use of a resource that is concurrently being torn down).

### Finding Description
`BidPool.stop()` closes `bp.bidMsgCh` and `bp.newBidCh` after CAS'ing `running` from 1 to 0: [1](#0-0) 

`AddBid` (reachable from the public `auction_submitBid` RPC via `AuctionAPI.SubmitBid`) checks `running` and then performs an unconditional, blocking send on `bp.newBidCh`: [2](#0-1) [3](#0-2) 

`HandleBid` (reachable from peer-relayed bid messages) checks `running` and then sends to `bp.bidMsgCh` inside a `select`/`default`: [4](#0-3) 

In Go, a send on a closed channel panics unconditionally and immediately — the `select` with `default` does not protect against this, since the closed-channel case is always ready. The window between the `atomic.LoadUint32(&bp.running)` check and the actual channel send is unbounded (it can span `validateBid`, `insertBid`, `getBidTxGasLimit`, RLP decode, signature verification, etc. in `AddBid`), giving ample opportunity for a concurrent `AuctionModule.Stop()` → `bp.stop()` call to close the channel in between.

`AuctionModule.Stop()` is invoked on node shutdown / module teardown: [5](#0-4) [6](#0-5) 

This mirrors the root cause of the Bluetooth CVE: a flag intended to gate queuing (`HCI_UNREGISTER` / `bp.running`) is checked non-atomically with the actual enqueue operation, so a concurrent teardown (`hci_unregister_dev` / `bp.stop()`) can race ahead, freeing/closing the underlying resource (`hdev` / channel) before the queued operation completes, leading to use of a torn-down resource.

### Impact Explanation
A successful race causes an unrecovered panic (`send on closed channel`) inside `AddBid` or `HandleBid`, which are invoked from goroutines spawned to serve RPC calls and peer bid propagation. Because these goroutines have no panic recovery around the channel send, the panic propagates and crashes the entire node process — a full denial of service for a CN (Consensus Node) participant in the auction subsystem, potentially disrupting block production and the fee-auction/gasless mechanism relying on it. This satisfies "state divergence between honest nodes"/availability-impact class of the acceptance criteria, since a crashed CN can no longer participate in consensus/proposal duties.

### Likelihood Explanation
The race requires `AuctionModule.Stop()` (module shutdown/restart, or `PostInsertBlock`'s indirect toggling doesn't close channels — only `bp.stop()`/`bp.start()` do that) to run concurrently with an in-flight `SubmitBid` RPC call or peer `HandleBid` invocation. This is realistic during node graceful shutdowns/restarts while bidding traffic is active (a public-RPC caller submitting a bid concurrently with node shutdown), and any external bidder/searcher can hold the race window open by controlling submission timing relative to observable node lifecycle events, or simply by high-frequency bid submission increasing the probability of overlap during a shutdown.

### Recommendation
Guard the channel sends with the same synchronization primitive used to close the channels (e.g., use a `select` with a `done`/`quit` channel that is closed in `stop()` instead of closing `bidMsgCh`/`newBidCh` directly), or protect close/send with a shared `sync.RWMutex` (readers hold the lock while sending, `stop()` takes the write lock before closing). Alternatively, wrap the channel sends in a `recover()`-protected helper so a panic cannot crash the whole process, and route bids through a mechanism that never closes a channel that producers may still write to.

### Proof of Concept
1. Start an auction-enabled Kaia CN node.
2. In a loop, repeatedly call `auction_submitBid` (or relay peer bid messages) concurrently while triggering `AuctionModule.Stop()` (e.g., node shutdown/restart cycle, or module reinitialization if exposed).
3. Because `AddBid`'s `atomic.LoadUint32(&bp.running)` check and its subsequent `bp.newBidCh <- bid` are separated by non-trivial validation work (`validateBid`, `insertBid`, `getBidTxGasLimit`), a concurrent `bp.stop()` call can close `newBidCh` in that window.
4. The subsequent `bp.newBidCh <- bid` in `AddBid` panics with `send on closed channel`, crashing the node process.

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

**File:** kaiax/auction/impl/init.go (L112-116)
```go
func (a *AuctionModule) Start() error {
	logger.Info("AuctionModule started")
	a.bidPool.start()
	return nil
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
