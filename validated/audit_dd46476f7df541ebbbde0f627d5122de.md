### Title
Unsynchronized double-fetch of `auction.Bid.GasLimit` between `BidPool.AddBid` and block-building bundle extraction causes non-deterministic gas-limit assignment on submitted bids - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid` inserts a bid into shared, mutex-protected maps (`bidMap`/`bidTargetMap`/`bidWinnerMap`) and only *afterwards*, outside of `bidMu`, computes and writes the bid's gas limit via `bid.SetGasLimit(gasLimit)`. Because the same `*auction.Bid` pointer is exposed to readers (via `GetTargetTxMap`) as soon as `insertBid` unlocks, a concurrent block-building goroutine can read `bid.GetGasLimit()` (through `GetBidTxGenerator`) in the window before `SetGasLimit` executes, mirroring the CVE-2017-9986 "double fetch" pattern where a shared pointer/field is written after being made visible for use, and readers can observe an intermediate (default/zero) value.

### Finding Description
`AddBid` performs the following sequence with no fetch-then-use atomicity across the insertion and mutation of the shared bid record: [1](#0-0) 

`insertBid` takes the write lock, publishes `bid` into `bp.bidMap`/`bp.bidTargetMap`/`bp.bidWinnerMap`, and releases the lock: [2](#0-1) 

Once `insertBid` returns and releases `bidMu`, the bid is fully "live" and reachable by other threads through `GetTargetTxMap`, which copies map entries but still returns the same underlying `*auction.Bid` pointer (not a deep copy): [3](#0-2) 

Concurrently, the block-building path calls `GetTargetTxMap` for the mining block and, for matched bids, builds the actual bid transaction via `GetBidTxGenerator(tx, bid)`: [4](#0-3) 

Only *after* `insertBid` has already made the bid visible does `AddBid` compute the real gas limit and mutate the shared struct with `bid.SetGasLimit(gasLimit)`: [5](#0-4) 

`bid.GasLimit` is a plain (non-atomic, non-mutex-protected) field on `auction.Bid`, so a concurrent `Get`/`Set` pair on the same pointer from two goroutines is a data race: the block-builder thread may read a stale/zero `GasLimit` for a bid that has already been admitted to the pool but not yet finalized by `AddBid`. This is the same root-cause shape as the double-fetch bug class in the report — a value is read and mutated via two independent, non-atomic accesses to shared state, and the outcome depends on interleaving timing rather than a single consistent snapshot.

### Impact Explanation
Depending on scheduler timing, different validators/CNs (which race the same bid through `handleBidMsg`/`AddBid` and simultaneously run block building via `ExtractTxBundles`) can observe different `GasLimit` values for the *same bid* at block-assembly time. Since `GetBidTxGenerator`/`GetBidTxGasLimit` (line 485-509) determines the constructed `BidTx`'s gas limit, and that gas limit is embedded in the resulting transaction/bundle, nodes racing this window differently could construct different `BidTx` payloads for the identical winning bid, leading to divergent block contents/hashes across honest nodes for the same bid input — a state-divergence class issue. At minimum, a bid consumed with a zero/stale gas limit could be built with insufficient gas, causing the bid's paid-for execution to run out of gas or be rejected, defeating the auction settlement guarantee that the highest bidder's transaction executes as promised.

### Likelihood Explanation
The race window is narrow but real: `AddBid` runs `insertBid` (which publishes the bid) and then unlocks before it calls `getBidTxGasLimit`/`SetGasLimit`. On a busy CN processing many bids near the block deadline (`EDOffset`) via `handleBidMsg` while the miner's `ExtractTxBundles` is invoked concurrently every block-building cycle, this timing overlap is plausible, especially under load or with an adversarial `Auctioneer`/searcher that intentionally floods bids right at the deadline to increase the chance of racing the block builder. No special privileges are needed — any entity able to call `auction_submitBid` (the public RPC, reachable by anyone) can trigger `AddBid`.

### Recommendation
Compute `getBidTxGasLimit` and call `bid.SetGasLimit` **before** the bid is published into `bp.bidMap`/`bp.bidTargetMap`/`bp.bidWinnerMap` inside `insertBid`, all under a single `bidMu.Lock()` critical section, so that no other goroutine can observe the bid via `GetTargetTxMap` until its `GasLimit` is finalized. Alternatively, protect `GasLimit` with its own mutex or atomic accessor, and have `GetTargetTxMap` return only bids whose gas limit has been finalized (e.g., a "ready" flag checked while holding `bidMu`).

### Proof of Concept
1. Start a CN with the auction module enabled and bid pool `running`.
2. Two goroutines execute concurrently against the same `BidPool`:
   - Goroutine A: calls `auction_submitBid` RPC repeatedly, which invokes `bp.AddBid(bid)` — `insertBid` (publishes `bid` under lock) → unlock → `bp.getBidTxGasLimit(bid)` → `bid.SetGasLimit(...)` (unprotected write).
   - Goroutine B: simulates the miner loop calling `a.bidPool.GetTargetTxMap(miningBlock)` then `a.GetBidTxGenerator(tx, bid)` reading `bid.GetGasLimit()` (unprotected read) in a tight loop right after each `AddBid` call returns from `insertBid`.
3. Observe (e.g., with `go test -race` or by logging read values) that Goroutine B can read `GasLimit == 0` (the pre-`SetGasLimit` value) for a bid already visible in `bidTargetMap`, demonstrating the unsynchronized double-fetch/write race on the shared `*auction.Bid`.

Note: I was not able to view the full `kaiax/auction/bid.go` file contents (only grep matches for `GasLimit`) due to index limits, so the exact field/method definitions of `GetGasLimit`/`SetGasLimit` (and whether any synchronization primitive wraps them) could not be fully confirmed from the index alone. If `SetGasLimit`/`GetGasLimit` do internally use a mutex or atomic value, the race described here would be mitigated at the field level, though the fundamental sequencing issue (publishing an incompletely-initialized bid before finalizing it) would still stand as a logical TOCTOU concern. A Devin session with full repo access should confirm this by opening `kaiax/auction/bid.go`.

### Citations

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

**File:** kaiax/auction/impl/builder.go (L31-70)
```go
func (a *AuctionModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	bundles := []*builder.Bundle{}
	curBlock := a.Chain.CurrentBlock()
	if curBlock == nil || atomic.LoadUint32(&a.bidPool.running) == 0 {
		return bundles
	}

	miningBlock := curBlock.NumberU64() + 1
	bidTargetMap := a.bidPool.GetTargetTxMap(miningBlock)
	if len(bidTargetMap) == 0 {
		return bundles
	}

	for _, tx := range txs {
		txHash := tx.Hash()
		bid, ok := bidTargetMap[txHash]
		if !ok {
			continue
		}
		b := builder.NewBundle(
			builder.NewTxOrGenList(a.GetBidTxGenerator(tx, bid)),
			txHash,
			true,
		)

		isConflict := false
		for _, prev := range append(prevBundles, bundles...) {
			if prev.IsConflict(b) {
				isConflict = true
				break
			}
		}
		if isConflict {
			continue
		}
		bundles = append(bundles, b)
	}

	return bundles
}
```
