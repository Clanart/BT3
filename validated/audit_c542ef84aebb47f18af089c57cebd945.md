### Title
Unsynchronized read of `BidPool.auctionEntryPointVersion` races with `updateAuctionInfo` writes, causing torn-string reads that corrupt auction bid gas-limit calculation - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.getBidTxGasLimit` reads the shared field `bp.auctionEntryPointVersion` without holding `bp.auctionInfoMu`, while `BidPool.updateAuctionInfo` (invoked from block-processing via `PostInsertBlock`) mutates that same field under the lock. This is the same bug class as CVE-2020-36219 (`atomic-option`): a shared mutable value accessed from multiple goroutines without consistent synchronization, causing a data race.

### Finding Description
`BidPool` protects `auctioneer`, `auctionEntryPoint`, `auctionEntryPointVersion`, and `bidTxGasBuffer` with `auctionInfoMu sync.RWMutex`: [1](#0-0) 

`updateAuctionInfo` writes all four fields together while holding the write lock, and is called on every block via `PostInsertBlock` -> `updateAuctionInfo`, i.e. from the block-import/consensus goroutine: [2](#0-1) [3](#0-2) 

`validateBidSigs` correctly reads `auctionEntryPoint`/`auctionEntryPointVersion` under `auctionInfoMu.RLock()`: [4](#0-3) 

However `getBidTxGasLimit`, which is invoked from `AddBid` on the bid-submission path, only takes the lock to read `bidTxGasBuffer`, releases it, and then reads `bp.auctionEntryPointVersion` unprotected: [5](#0-4) [6](#0-5) 

`AddBid` is reachable directly from an unprivileged, network-facing path: `HandleBid` enqueues attacker/bidder-supplied bids from p2p into `bidMsgCh`, which `handleBidMsg` drains and feeds into `AddBid` concurrently with block processing: [7](#0-6) [8](#0-7) 

Because a Go `string` is a two-word header (pointer + length), a concurrent unsynchronized write in `updateAuctionInfo` racing with the unprotected read in `getBidTxGasLimit` can produce a torn read — a pointer/length pair that never existed as a single assigned value. This is undefined behavior under the Go memory model (flagged by `-race`) and can yield garbage data, an out-of-bounds/invalid string, or a crash, matching the underlying CWE-662 (improper synchronization) root cause of the reported `atomic-option` advisory.

### Impact Explanation
The torn/garbage `auctionEntryPointVersion` value is passed into `system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)`, which selects the ABI/typehash used to encode the auction call data and, consequently, the intrinsic gas computed for the generated bid transaction (`getBidTxGasLimit`'s return value is stored via `bid.SetGasLimit`). An incorrect or corrupted version string can cause:
- computation of an intrinsic gas / call-data encoding mismatched with what is actually used later when the block builder encodes the winning bid transaction (`GetBidTxGenerator` in `kaiax/auction/impl/getter.go`), leading to state divergence between nodes that observe the race differently, or
- a node crash/panic on malformed string data, which is a liveness impact for a public-RPC/p2p-reachable component.

Because the race is timing-dependent and depends on Go's internal memory representation, worst-case behavior (crash vs. garbage value) cannot be fully bounded without runtime reproduction; this is a genuine data race but its concrete “unauthorized value movement” consequence is not guaranteed on every race hit.

### Likelihood Explanation
`updateAuctionInfo` runs on essentially every block once auctions are enabled (via `PostInsertBlock`), and `AddBid`/`getBidTxGasLimit` runs whenever any external party submits a bid via the p2p `HandleBid` path or the auction API — both are attacker-reachable with no special privilege. The two paths race against the same unlocked field on a routine, frequent basis, so the race window is realistically hit under normal auction traffic, not just adversarial timing.

### Recommendation
In `getBidTxGasLimit`, read `auctionEntryPointVersion` under the same `auctionInfoMu.RLock()` critical section as `bidTxGasBuffer` (return a consistent snapshot of both fields together), matching the pattern already used in `validateBidSigs`: [5](#0-4) 
Audit other direct field accesses of `auctioneer`/`auctionEntryPoint`/`auctionEntryPointVersion`/`bidTxGasBuffer` throughout `kaiax/auction/impl` to ensure every read/write path holds `auctionInfoMu`, e.g. via `go test -race` on the auction bid pool test suite.

### Proof of Concept
1. Start the auction module with a non-empty `auctionEntryPointVersion` set via `updateAuctionInfo` (block N).
2. Concurrently:
   - Goroutine A: import block N+1, causing `PostInsertBlock` -> `updateAuctionInfo` to write new `auctioneer/auctionEntryPoint/auctionEntryPointVersion/bidTxGasBuffer` values (e.g., version changes length, such as `"1.0"` -> `"1"`).
   - Goroutine B: submit a bid via p2p `HandleBid`, which is drained by `handleBidMsg` -> `AddBid` -> `getBidTxGasLimit`, reading `bp.auctionEntryPointVersion` unprotected.
3. Run with `go test -race ./kaiax/auction/...` targeting a test that exercises `updateAuctionInfo` and `AddBid` concurrently (similar to `tests/race_test.go`'s existing race harness pattern) to observe the detected data race on `auctionEntryPointVersion`.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L54-58)
```go
	auctionInfoMu            sync.RWMutex
	auctioneer               common.Address
	auctionEntryPoint        common.Address
	auctionEntryPointVersion string
	bidTxGasBuffer           uint64
```

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

**File:** kaiax/auction/impl/bid_pool.go (L252-269)
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
```

**File:** kaiax/auction/impl/bid_pool.go (L397-416)
```go
func (bp *BidPool) validateBidSigs(bid *auction.Bid) error {
	bp.auctionInfoMu.RLock()
	defer bp.auctionInfoMu.RUnlock()

	if bid.SearcherSig == nil || len(bid.SearcherSig) != crypto.SignatureLength {
		return auction.ErrInvalidSearcherSig
	}
	if bid.AuctioneerSig == nil || len(bid.AuctioneerSig) != crypto.SignatureLength {
		return auction.ErrInvalidAuctioneerSig
	}

	// Verify the EIP712 signature.
	if err := bid.ValidateSearcherSig(bp.ChainConfig.ChainID, bp.auctionEntryPoint, bp.auctionEntryPointVersion); err != nil {
		return err
	}

	// Verify the auctioneer signature.
	if err := bid.ValidateAuctioneerSig(bp.auctioneer); err != nil {
		return err
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

**File:** kaiax/auction/impl/bid_pool.go (L457-469)
```go
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

**File:** kaiax/auction/impl/bid_pool.go (L485-490)
```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	bp.auctionInfoMu.RLock()
	buffer := bp.bidTxGasBuffer
	bp.auctionInfoMu.RUnlock()

	data, err := system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)
```

**File:** kaiax/auction/impl/execution.go (L55-63)
```go
func (a *AuctionModule) updateAuctionInfo(num *big.Int) bool {
	auctioneer := common.Address{}
	auctionEntryPointAddr := common.Address{}
	auctionEntryPointVersion := ""
	bidTxGasBuffer := uint64(0)

	defer func() {
		a.bidPool.updateAuctionInfo(auctioneer, auctionEntryPointAddr, auctionEntryPointVersion, bidTxGasBuffer)
	}()
```
