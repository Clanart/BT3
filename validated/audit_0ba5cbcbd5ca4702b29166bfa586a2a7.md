### Title
Auction bid pool is silently wiped on any auction-parameter change, dropping already-submitted searcher bids without an in-use check - (File: `kaiax/auction/impl/bid_pool.go`)

### Summary
The Kaia auction module (KIP-249) mirrors the Backd `InflationManager.removeKeeperGauge` pattern: a governance/registry-driven configuration change unconditionally tears down a resource (the bid pool) that other parties (searchers/bidders) currently depend on, with no check for in-flight usage before the reset.

### Finding Description
`BidPool.updateAuctionInfo` is invoked from `PostInsertBlock` on every new block via `AuctionModule.updateAuctionInfo`, which re-reads `AuctionEntryPoint`, `Auctioneer`, `AuctionEntryPointVersion`, and `bidTxGasBuffer` from the `SystemRegistry`/`AuctionEntryPoint` contracts. [1](#0-0) 
If any of these four values differ from the cached ones, the pool calls `clearBidPool()`, which wipes `bidMap`, `bidTargetMap`, and `bidWinnerMap` in one shot, with no check whether any of the currently pooled bids are still valid, about to win, or already "in use" for the upcoming block: [2](#0-1) 
`clearBidPool` itself performs an unconditional reset: [3](#0-2) 

This is architecturally identical to the reported bug class: a privileged/administrative action (here, a `Registry`/`AuctionEntryPoint` parameter change, analogous to `InflationManager.removeKeeperGauge`) invalidates a dependency (`bidPool` entries, analogous to the `KeeperGauge`) that other unprivileged users (`searchers who submitted bids`, analogous to `Alice`'s registered top-up action) are relying on for near-future execution, with no on-chain check that the dependency is currently "in use" (i.e., that bids target transactions about to be included).

### Impact Explanation
Searchers who have already had their bid accepted by the `Auctioneer` and forwarded into a CN's bid pool (`BidPool.AddBid` / `insertBid`) lose their bid the moment any of `auctioneer`, `auctionEntryPoint`, `auctionEntryPointVersion`, or `bidTxGasBuffer` changes on-chain — even if that bid targets a transaction that is about to be included in the very next block. Because `updateAuctionInfo` is called unconditionally at the top of every `PostInsertBlock`, a single governance/registry transaction changing any tracked parameter silently drops all currently pooled bids before `removeOldBids` even runs, denying searchers the auction settlement they already paid/committed for. This matches the confirmed C4 Medium classification of the original finding: "potential loss of yield (or delay)" and "system for fees/settlement can be stopped due to external conditions."

### Likelihood Explanation
This requires only that governance (or whoever controls the `SystemRegistry`/`AuctionEntryPoint` contract) update the entry point, auctioneer, version, or gas-buffer parameter — an expected, periodic operational action rather than an attack — while searcher bids are in flight in the pool. Given the module recomputes and compares these values on every block (`PostInsertBlock`), any legitimate parameter rotation immediately triggers the full-pool wipe, making the likelihood of hitting the DoS window straightforward once a rotation is scheduled without coordinating with pending bids.

### Recommendation
Before clearing the bid pool in `updateAuctionInfo`, add an on-chain/off-chain safeguard such as: only clearing bids whose `blockNumber` values are unaffected, or delaying parameter changes with a timelock large enough to guarantee no pending bids target blocks still valid under the old configuration, similar to the "on-chain check that the resource is not in use" mitigation recommended in the original report.

### Proof of Concept
1. A searcher submits a bid for `blockNumber = N` via `auction_submitBid`; the CN's `BidPool.AddBid` inserts it into `bidMap`/`bidTargetMap`/`bidWinnerMap`. [4](#0-3) 
2. Before block `N` is produced, governance submits a `Registry`/`AuctionEntryPoint` transaction that changes `bidTxGasBuffer` (or any tracked auction parameter).
3. On `PostInsertBlock` for the block containing that governance transaction, `updateAuctionInfo` detects the parameter change and calls `bp.clearBidPool()`, wiping the searcher's bid for block `N`. [5](#0-4) 
4. When block `N` is produced, the target transaction executes without the searcher's accompanying bid transaction, and the searcher's auction opportunity is lost with no on-chain warning or recourse — mirroring Alice's un-executed top-up action after her `KeeperGauge` was killed.

### Citations

**File:** kaiax/auction/impl/execution.go (L52-64)
```go
// updateAuctionInfo updates the auctioneer address and auction entry point address for the given block number.
// It expects the `num` is after Randao fork.
// It returns true if the non-zero auctioneer address and auction entry point address are set, otherwise false.
func (a *AuctionModule) updateAuctionInfo(num *big.Int) bool {
	auctioneer := common.Address{}
	auctionEntryPointAddr := common.Address{}
	auctionEntryPointVersion := ""
	bidTxGasBuffer := uint64(0)

	defer func() {
		a.bidPool.updateAuctionInfo(auctioneer, auctionEntryPointAddr, auctionEntryPointVersion, bidTxGasBuffer)
	}()

```

**File:** kaiax/auction/impl/bid_pool.go (L185-193)
```go
// clearBidPool clears the bid pool.
func (bp *BidPool) clearBidPool() {
	bp.bidMu.Lock()
	defer bp.bidMu.Unlock()

	bp.bidMap = make(map[common.Hash]*auction.Bid)
	bp.bidTargetMap = make(map[uint64]map[common.Hash]*auction.Bid)
	bp.bidWinnerMap = make(map[uint64]map[common.Address]common.Hash)
}
```

**File:** kaiax/auction/impl/bid_pool.go (L195-213)
```go
// updateAuctionInfo updates the auction info if the auctioneer or auction entry point address is changed.
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

**File:** kaiax/auction/impl/bid_pool.go (L276-324)
```go
func (bp *BidPool) insertBid(bid *auction.Bid) error {
	bp.bidMu.Lock()
	defer bp.bidMu.Unlock()

	var (
		blockNumber  = bid.BlockNumber
		targetTxHash = bid.TargetTxHash
		sender       = bid.Sender
	)

	// Re-check bidWinnerMap here — two concurrent bids can pass validateBid together.
	if _, ok := bp.bidMap[bid.Hash()]; ok {
		return auction.ErrBidAlreadyExists
	}
	if bp.senderHasDifferentWinner(bid) {
		return auction.ErrBidSenderExists
	}

	// If same block number, same target tx hash exists, replace it if it's better
	if existingBid, ok := bp.bidTargetMap[blockNumber][targetTxHash]; ok {
		// FCFS if the bid is the same.
		if existingBid.Bid.Cmp(bid.Bid) >= 0 {
			return auction.ErrLowBid
		}

		logger.Trace("Replace bid", "old", existingBid.Hash(), "new", bid.Hash())
		delete(bp.bidMap, existingBid.Hash())
		delete(bp.bidWinnerMap[blockNumber], existingBid.Sender)
	} else {
		if int64(len(bp.bidMap)) >= bp.maxBidPoolSize {
			logger.Info("Bid pool is full", "maxBidPoolSize", bp.maxBidPoolSize, "bid", bid.Hash())
			return auction.ErrBidPoolFull
		}
	}

	hash := bid.Hash()

	bp.initializeBidMap(blockNumber)

	bp.bidMap[hash] = bid
	bp.bidTargetMap[blockNumber][targetTxHash] = bid
	bp.bidWinnerMap[blockNumber][sender] = hash

	numBidsGauge.Update(int64(len(bp.bidMap)))

	logger.Trace("Add bid", "bid", hash)

	return nil
}
```
