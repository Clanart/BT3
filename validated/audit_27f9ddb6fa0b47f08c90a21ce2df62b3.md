### Title
Race Between Auction-Epoch Rotation and Bid Admission Allows a Bid Authorized by a Stale Auctioneer to Enter the Live Bid Pool - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
The pjproject advisory describes a heap use-after-free caused by a race between ICE **session destruction** and in-flight **callbacks** that still reference the dying session. `BidPool` in the Kaia auction module has the same class of bug at the state-machine level: `AddBid` (the "callback" that validates and admits a searcher's bid) is not synchronized with `updateAuctionInfo`/`clearBidPool` (the "session destruction" that rotates the auctioneer/entry-point and wipes the pool on every new block). A bid validated against the *old* auctioneer can still be written into the pool *after* the epoch has rotated.

### Finding Description
`AddBid` performs validation and insertion as two separate, non-atomic critical sections: [1](#0-0) 

`validateBid` → `validateBidSigs` reads the auctioneer/entry-point/version under `auctionInfoMu.RLock()` at time T1 and verifies `bid.AuctioneerSig` against whatever `bp.auctioneer` is at that instant: [2](#0-1) 

`insertBid` then takes `bidMu.Lock()` independently, at time T2, and unconditionally writes the bid into `bidMap`/`bidTargetMap`/`bidWinnerMap` — it never re-checks that the auction epoch (auctioneer, entry point, version) is still the one the bid was validated against: [3](#0-2) 

Concurrently, on **every block** (not an admin/rare event), `PostInsertBlock` calls `updateAuctionInfo`, which reads the new auctioneer/entry-point/version from the system registry and, if they changed, calls `bp.updateAuctionInfo`, which clears the pool and swaps in the new epoch's fields under `bidMu`/`auctionInfoMu`: [4](#0-3) [5](#0-4) 

Because `validateBidSigs` (T1, old epoch) and `insertBid` (T2, possibly new epoch) are two independent lock acquisitions with no shared epoch token, the following interleaving is possible:

1. Searcher bid is signed/co-signed by the current auctioneer A₁ and submitted; `handleBidMsg`/`AddBid` reads `bp.auctioneer == A₁` and successfully verifies the signature.
2. A new block is inserted; `PostInsertBlock` → `updateAuctionInfo` detects the on-chain auctioneer rotated to A₂ (or the entry point/version changed) and calls `clearBidPool()`, wiping `bidMap`/`bidTargetMap`/`bidWinnerMap` and swapping `bp.auctioneer/entryPoint/version` to the A₂ epoch — this is the "session destruction" analog.
3. The still-in-flight goroutine from step 1 now acquires `bidMu.Lock()` in `insertBid` *after* the clear and inserts the bid (validated only under A₁'s authority) into the now-live pool that is supposed to belong exclusively to auctioneer A₂'s epoch.
4. `getBidTxGasLimit`, called right after `insertBid` inside `AddBid`, re-reads `bp.auctionEntryPointVersion`/`bp.bidTxGasBuffer` — which have already advanced to the new epoch's values — and uses them to build the call data for a bid whose cryptographic authorization never covered this epoch: [6](#0-5) 

5. This bid now sits in `bidTargetMap`/`bidWinnerMap` and is handed to the block-assembly worker via `GetTargetTxMap`, which the worker uses to build the winning `BidTx` bundle for that target transaction: [7](#0-6) 

No code path re-validates that a bid's authorizing epoch (auctioneer/entry point/version at validation time) still matches the epoch under which it is ultimately selected and executed. The `clearBidPool()` call is meant to be the authoritative destruction boundary between auction epochs (exactly as `MatcherSession.Close()` is meant to be an authoritative boundary in the bloombits code, for contrast), but `AddBid`'s check-then-act sequence can straddle that boundary.

### Impact Explanation
This breaks the intended invariant that only bids co-signed by the *currently active* auctioneer for the *currently active* `AuctionEntryPoint`/version can win a block's auction slot. A bid whose authorization was granted by a since-rotated (stale) auctioneer can be smuggled into the live pool and selected for the next block's bundle, effectively bypassing the current auctioneer's exclusive right to sanction MEV-auction execution for that block — a settlement-authorization bypass in the gasless/auction pipeline reachable purely by a normal, unprivileged auction bidder submitting a bid at the right moment relative to a block boundary (which happens on every block, making the race window recurring rather than a one-off).

### Likelihood Explanation
`updateAuctionInfo`/`clearBidPool` runs on every `PostInsertBlock` call, i.e., every block after the Randao fork, so the destruction/rotation event is frequent. `AddBid` is invoked both from the peer-gossip path (`HandleBid` → `bidMsgCh` → `handleBidMsg` → `AddBid`) and, per the module's README, from the `auction_submitBid` RPC, so any bidder can trigger the validate/insert sequence at will and race it against block production without needing any special privilege — only tight timing around a block boundary where the auctioneer/version happens to change (governance-driven rotations, entry-point redeployments, or version bumps).

### Recommendation
Make bid validation and insertion atomic with respect to the auction epoch: capture an epoch identifier (or generation counter incremented in `updateAuctionInfo`) while holding `auctionInfoMu`, pass it through to `insertBid`, and reject the insert (and the corresponding `newBidCh` push / gas-limit computation) if the pool's current epoch no longer matches the one the bid was validated against — i.e., perform "check-signature-and-insert" under a single lock scope keyed to the epoch, rather than two independently-locked phases.

### Proof of Concept
Not exploitable via a static PoC snippet because it depends on goroutine scheduling; the race can be demonstrated with `go test -race` by:
1. Starting a `BidPool` with auctioneer A₁/entryPoint E₁/version V₁ and `running=1`.
2. Concurrently: goroutine G1 repeatedly calls `bp.AddBid(bidSignedByA1)`; goroutine G2 repeatedly calls `bp.updateAuctionInfo(A2, E2, V2, buffer2)` (simulating `PostInsertBlock` on the next block).
3. Observe that `bp.bidMap`/`bp.bidTargetMap` can end up containing `bidSignedByA1` even after `updateAuctionInfo` has switched `bp.auctioneer` to A₂, i.e., a bid validated under A₁ appears in the pool that `GetTargetTxMap` exposes for the A₂ epoch — confirming the missing epoch-fencing between `validateBidSigs` (bid_pool.go:397-419), `insertBid` (bid_pool.go:276-324), and `updateAuctionInfo`/`clearBidPool` (bid_pool.go:196-213), driven every block by `PostInsertBlock` (execution.go:29-50).

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

**File:** kaiax/auction/impl/bid_pool.go (L240-248)
```go
func (bp *BidPool) GetTargetTxMap(num uint64) map[common.Hash]*auction.Bid {
	bp.bidMu.RLock()
	defer bp.bidMu.RUnlock()

	targetTxMap := make(map[common.Hash]*auction.Bid)
	maps.Copy(targetTxMap, bp.bidTargetMap[num])

	return targetTxMap
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

**File:** kaiax/auction/impl/bid_pool.go (L397-419)
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

	return nil
}
```

**File:** kaiax/auction/impl/bid_pool.go (L485-509)
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
	if err != nil {
		return 0, err
	}
	floorDataGas := uint64(0)
	if rules.IsPrague {
		floorDataGas, err = blockchain.FloorDataGas(types.TxTypeEthereumDynamicFee, data, 0)
		if err != nil {
			return 0, err
		}
	}

	return max(intrinsicGas+bid.CallGasLimit+buffer, floorDataGas), nil
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
