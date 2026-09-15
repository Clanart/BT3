### Title
Unsynchronized read of `BidPool.auctionEntryPointVersion` causes a data race between block-insertion updates and bid submission - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.getBidTxGasLimit` reads the shared field `bp.auctionEntryPointVersion` directly, without acquiring `bp.auctionInfoMu`, while every new block causes `PostInsertBlock` → `updateAuctionInfo` to overwrite the very same field under the lock. This is the same bug class as CVE-2020-36207 (aovec): a shared mutable value that is written and read from different goroutines without a consistent locking discipline, producing a data race and potential memory corruption of the string value.

### Finding Description
`BidPool.auctionEntryPointVersion` is a `string` field protected — inconsistently — by `bp.auctionInfoMu`:

- Writer: `BidPool.updateAuctionInfo` takes `auctionInfoMu.Lock()` and assigns `bp.auctionEntryPointVersion = auctionEntryPointVersion` [1](#0-0) . This is invoked from `AuctionModule.PostInsertBlock` on every newly inserted block [2](#0-1) , i.e. from the chain-insertion goroutine, completely independent of any particular caller.
- Reader with correct locking: `GetAuctionEntryPointVersion()` correctly takes `auctionInfoMu.RLock()` [3](#0-2) , and `getBidTxGasLimit` itself correctly locks to read the sibling field `bidTxGasBuffer` [4](#0-3) .
- Reader with missing lock: immediately after releasing the lock, the very same function reads `bp.auctionEntryPointVersion` directly (unlocked) and passes it into `system.EncodeAuctionCallData` [5](#0-4) .

`getBidTxGasLimit` is called from `AddBid` → `insertBid`-adjacent path on every bid submission [6](#0-5) , which is reachable by any unprivileged auction bidder submitting a bid via `HandleBid`/the bid submission RPC path (`bp.bidMsgCh` → `handleBidMsg` → `AddBid`) [7](#0-6) .

Because block insertion happens continuously and independently of bid submission timing, a bidder can trigger `AddBid`/`getBidTxGasLimit` at the same moment a new block causes `updateAuctionInfo` to rewrite `auctionEntryPointVersion`. A Go `string` is a two-word header (pointer + length); concurrent unsynchronized read/write of it is a data race that can produce a torn value — a pointer/length pair that were never valid together — leading to an out-of-bounds read or crash when the value is subsequently used to index the auction version→ABI/typehash table in `EncodeAuctionCallData`.

### Impact Explanation
This directly parallels the aovec advisory: a value shared across goroutines without a `Send`/`Sync`-safe protocol equivalent (a consistent mutex discipline) is read concurrently with mutation elsewhere. In Go this manifests as a detectable data race with the potential for a corrupted string header, which can crash the node process (denial of service on validators/bidders' local node) or, in the worst case, cause `EncodeAuctionCallData` to select the wrong EIP-712 typehash/ABI for a bid, potentially producing malformed auction calldata / bid validation inconsistencies between nodes that observe the race differently at slightly different times.

### Likelihood Explanation
Any account may act as an auction bidder and call the bid-submission path repeatedly and continuously; block insertion (and thus `updateAuctionInfo`) happens on the normal chain-progression cadence and is fully out of the bidder's control but highly frequent and predictable (every block). No special privilege is required — an unprivileged bidder simply needs to submit bids at a sustained rate to increase the probability of overlapping with a block-insertion boundary. The bug is a genuine, reachable data race, not merely resource exhaustion.

### Recommendation
Take `bp.auctionInfoMu.RLock()` around the read of `bp.auctionEntryPointVersion` in `getBidTxGasLimit` (or capture both `bidTxGasBuffer` and `auctionEntryPointVersion` together under a single lock acquisition), so that all accesses to `BidPool`'s auction-info fields go through the same locking discipline used elsewhere (e.g. `GetAuctionEntryPointVersion`, `validateBidSigs`).

### Proof of Concept
Not independently reproducible from the given tool access with a race-detector run, but the code path is concretely demonstrable:
1. Run a node with the auction module active and Randao fork enabled so `PostInsertBlock` runs on every block [2](#0-1) .
2. Have a client continuously submit valid bids via the bid RPC/gossip path so `AddBid` → `getBidTxGasLimit` executes concurrently with block production [6](#0-5) [8](#0-7) .
3. Building/running with `-race` on this workload will flag a data race on `bp.auctionEntryPointVersion` between `updateAuctionInfo` (writer, locked) and `getBidTxGasLimit` (reader, unlocked).

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L196-213)
```go
func (bp *BidPool) updateAuctionInfo(auctioneer common.Address, auctionEntryPoint common.Address, auctionEntryPointVersion string, bidTxGasBuffer uint64) {
	bp.auctionInfoMu.Lock()
	defer bp.auctionInfoMu.Unlock()

	if bp.auctioneer == auctioneer && bp.auctionEntryPoint == auctionEntryPoint && bp.auctionEntryPointVersion == auctionEntryPointVersion && bp.bidTxGasBuffer == bidTxGasBuffer {
		return
	}

	// Clear the existing auction pool since the auctioneer or auction entry point address is changed.
	bp.clearBidPool()

	bp.auctioneer = auctioneer
	bp.auctionEntryPoint = auctionEntryPoint
	bp.auctionEntryPointVersion = auctionEntryPointVersion
	bp.bidTxGasBuffer = bidTxGasBuffer

	logger.Info("Update auction info", "auctioneer", auctioneer, "auctionEntryPoint", auctionEntryPoint, "auctionEntryPointVersion", auctionEntryPointVersion, "bidTxGasBuffer", bidTxGasBuffer)
}
```

**File:** kaiax/auction/impl/bid_pool.go (L234-238)
```go
func (bp *BidPool) GetAuctionEntryPointVersion() string {
	bp.auctionInfoMu.RLock()
	defer bp.auctionInfoMu.RUnlock()
	return bp.auctionEntryPointVersion
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

**File:** kaiax/auction/impl/bid_pool.go (L421-469)
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

// checkRateLimit checks if the peer is within rate limit
func (bp *BidPool) checkRateLimit(peerID string) bool {
	bp.peerRateLimiterMu.Lock()
	defer bp.peerRateLimiterMu.Unlock()

	limiter, exists := bp.peerRateLimiter.Get(peerID)
	if !exists {
		// Create new rate limiter for this peer
		// Use burst equal to the rate limit (we only use rate limit, not the burst)
		limiter = rate.NewLimiter(rate.Limit(bidsPerSecondPerPeer), bidsPerSecondPerPeer)
		bp.peerRateLimiter.Add(peerID, limiter)
	}

	// It'll simply discard the bid if the rate limit is exceeded
	// We don't need to reserve for a bid here because the original bid will be sent from auctioneer through different channel (see #api.SubmitBid)
	return limiter.(*rate.Limiter).Allow()
}

func (bp *BidPool) handleBidMsg() {
	defer bp.wg.Done()

	for {
		select {
		case bid, ok := <-bp.bidMsgCh:
			if !ok {
				return
			}
			bp.AddBid(bid)
		}
	}
}
```

**File:** kaiax/auction/impl/bid_pool.go (L485-494)
```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	bp.auctionInfoMu.RLock()
	buffer := bp.bidTxGasBuffer
	bp.auctionInfoMu.RUnlock()

	data, err := system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)
	if err != nil {
		return 0, err
	}

```

**File:** kaiax/auction/impl/execution.go (L29-50)
```go
func (a *AuctionModule) PostInsertBlock(block *types.Block) error {
	if a.Downloader.Synchronising() || !a.ChainConfig.IsRandaoForkEnabled(block.Number()) {
		atomic.CompareAndSwapUint32(&a.bidPool.running, 1, 0)
		return nil
	}

	if !a.updateAuctionInfo(block.Number()) {
		logger.Debug("stop auction since auctioneer or auction entry point is not set")
		atomic.CompareAndSwapUint32(&a.bidPool.running, 1, 0)
		return nil
	}

	atomic.CompareAndSwapUint32(&a.bidPool.running, 0, 1)

	txHashMap := make(map[common.Hash]struct{})
	for _, tx := range block.Transactions() {
		txHashMap[tx.Hash()] = struct{}{}
	}
	a.bidPool.removeOldBids(block.Number().Uint64(), txHashMap)

	return nil
}
```
