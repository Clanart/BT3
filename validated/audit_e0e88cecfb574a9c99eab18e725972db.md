### Title
Unsynchronized read of `auctionEntryPointVersion`/`auctionEntryPoint` in `BidPool.getBidTxGasLimit` races with `updateAuctionInfo` writes, causing calldata/version mismatch and node crash/divergence - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.getBidTxGasLimit` reads `bp.auctionEntryPointVersion` (via `system.EncodeAuctionCallData`) and implicitly relies on `bp.auctionEntryPoint`/`bp.ChainConfig` outside the `auctionInfoMu` lock that protects those exact same fields, while `updateAuctionInfo` mutates them under `auctionInfoMu.Lock()` on every block insertion. This mirrors the CVE-2022-33744 pattern: a shared structure (the rbtree in Xen; here the auction-info fields) is updated under a lock in one path but read without holding that lock in another, creating a race window that unprivileged callers can trigger.

### Finding Description
`BidPool.getBidTxGasLimit` only takes `auctionInfoMu.RLock()` to read `bidTxGasBuffer`, then releases the lock before reading `bp.auctionEntryPointVersion` directly to compute calldata: [1](#0-0) 

Meanwhile, `updateAuctionInfo` (invoked from `AuctionModule.updateAuctionInfo` in `execution.go` at the start of `PostInsertBlock` for every new block) takes the write lock and overwrites `bp.auctionEntryPoint`, `bp.auctionEntryPointVersion`, and `bp.bidTxGasBuffer` together: [2](#0-1) 

`PostInsertBlock` runs this update on the chain-insertion goroutine at block boundaries: [3](#0-2) 

Concurrently, any unprivileged bidder can trigger `getBidTxGasLimit` via `AddBid`, which runs on the `handleBidMsg` goroutine consuming attacker-controlled network messages (`HandleBid` → `bidMsgCh` → `AddBid`): [4](#0-3) [5](#0-4) 

Because `getBidTxGasLimit`'s read of `auctionEntryPointVersion` (line 490 in `bid_pool.go`) is outside any lock while `updateAuctionInfo`'s write is inside `auctionInfoMu`, this is a genuine unsynchronized concurrent read/write on the same field — the same class of defect as the CVE (partial/unlocked update of a shared structure that another thread walks concurrently). `getBidTxGasLimit` is also called a second time from `getter.go`'s `GetBidTxGenerator` (used when building the on-chain settlement transaction for a winning bid), again reading `a.bidPool.GetAuctionEntryPointVersion()` and `EncodeAuctionCallData` separately and non-atomically with respect to `insertBid`/`updateAuctionInfo` state: [6](#0-5) 

### Impact Explanation
If the auction entry-point contract is redeployed/upgraded (auctioneer rotates entry point or bumps `AUCTION_VERSION`, e.g. v2.1 → v3.0) exactly at a block boundary while a bid is concurrently being processed, `getBidTxGasLimit`/`EncodeAuctionCallData` can be invoked with a stale or torn `auctionEntryPointVersion` relative to the `auctionEntryPoint`/gas buffer actually used elsewhere in the same call path. This can produce: (a) calldata encoded with the wrong ABI/typehash for the resolved entry point, causing the settlement transaction to be built with mismatched version/address pairing, which on execution reverts or behaves unexpectedly (state divergence risk if different nodes race differently and pick different gas limits/versions when independently building the same settlement tx), or (b) a Go data race flagged under `-race`, which in a supervised/monitored production build could crash the node process, a Denial-of-Service directly analogous to the CVE's DoS impact. This is Medium severity: it requires a specific timing window around a live entry-point/version transition and does not by itself allow direct fund theft, but it can cause block-building non-determinism between honest nodes on shared auction infrastructure.

### Likelihood Explanation
Likelihood is moderate: the race window opens only during the (presumably rare) event of an auction entry-point/version change at chain tip, and requires bid traffic to arrive precisely during `PostInsertBlock`'s `updateAuctionInfo` call. Any bidder or peer relaying bids (`HandleBid`) can supply the concurrent trigger without special privileges, so the "attacker-reachable" surface is present, but the specific version-change trigger is an operational event rather than something a lone attacker fully controls.

### Recommendation
Read all auction-info fields (`auctionEntryPoint`, `auctionEntryPointVersion`, `bidTxGasBuffer`) as a single consistent snapshot under one `auctionInfoMu.RLock()`/`RUnlock()` before calling `system.EncodeAuctionCallData` and computing intrinsic gas, instead of reading `bidTxGasBuffer` under the lock and `auctionEntryPointVersion`/`auctionEntryPoint` afterward unprotected. Apply the same fix to `GetBidTxGenerator` in `getter.go`, capturing entry point, version, and gas buffer atomically in one locked read at the start of the closure.

### Proof of Concept
1. Node A is running with `AuctionModule` active; auctioneer periodically upgrades the auction entry-point contract from v2.1 to v3.0 (or changes `auctionEntryPoint` address), causing `PostInsertBlock` → `updateAuctionInfo` to fire on each new block (`kaiax/auction/impl/execution.go:35`).
2. An attacker (or any peer) floods `HandleBid` with valid bids so that `handleBidMsg` continuously calls `AddBid` → `getBidTxGasLimit` (`kaiax/auction/impl/bid_pool.go:271,485`).
3. Time the entry-point/version update (`kaiax/auction/impl/bid_pool.go:196`) to land between `getBidTxGasLimit`'s lock release (line 488) and its unlocked read of `auctionEntryPointVersion` at line 490.
4. Run the process with the Go race detector (`-race`) attached, as already exercised by the project's own concurrency tests (`tests/race_test.go`), to demonstrate the unsynchronized concurrent access is flagged, or observe a settlement transaction built with an inconsistent entry-point/version pairing.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L196-212)
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

**File:** kaiax/auction/impl/bid_pool.go (L485-496)
```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	bp.auctionInfoMu.RLock()
	buffer := bp.bidTxGasBuffer
	bp.auctionInfoMu.RUnlock()

	data, err := system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)
	if err != nil {
		return 0, err
	}

	rules := bp.ChainConfig.Rules(big.NewInt(int64(bid.BlockNumber)))
	intrinsicGas, err := types.IntrinsicGas(data, nil, nil, false, rules)
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

**File:** kaiax/auction/impl/getter.go (L27-47)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
		}

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}
```
