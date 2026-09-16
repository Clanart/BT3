Based on my investigation, I found a strong analog in the auction bid pool module.

### Title
Unauthorized DoS lock-out of auction bidder due to missing revert of bid-pool state when `AddBid` fails after `insertBid` succeeds - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid` performs a state transition (`insertBid`) that registers the bid as the sender's committed "winner" for a given block *before* a later step (`getBidTxGasLimit`) that can still fail. When that later step fails, `AddBid` returns an error to the caller but never rolls back the bid it just inserted into `bidMap`, `bidTargetMap`, and `bidWinnerMap`. This mirrors the reported bug class: a resource is transitioned to a "committed/processing" state, a subsequent step fails, and no explicit revert path exists.

### Finding Description
`AddBid` executes in this order: [1](#0-0) 
1. `validateBid` — sanity/signature checks.
2. `insertBid` — writes the bid into `bp.bidMap`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and crucially `bp.bidWinnerMap[blockNumber][sender] = hash` [2](#0-1) .
3. `getBidTxGasLimit(bid)` — computed *after* the bid has already been committed to the pool.
4. Only on success is the bid dispatched via `bp.newBidCh <- bid` for further processing.

If step 3 returns an error, `AddBid` returns `(common.Hash{}, err)` immediately — there is no call to remove the bid from `bidMap`/`bidTargetMap`/`bidWinnerMap`. The bid remains permanently registered as that sender's winning bid for `blockNumber` until `removeOldBids` eventually prunes it once the block number passes [3](#0-2) .

Because `validateBid` and `insertBid` both consult `senderHasDifferentWinner`, which checks whether the sender already has a *different* bid registered as winner for that block [4](#0-3) , any subsequent bid submission by the same sender for the same block — even a legitimately higher bid on a different target, or a corrected bid — is rejected with `ErrBidSenderExists` unless it is bit-for-bit identical to the stuck bid (which then hits `ErrBidAlreadyExists` in `validateBid`). This is the exact analog of the promo stuck-in-`processing` bug: an intermediate state-mutating step (`insertBid`) is not paired with a revert in the error path of a later, fallible step (`getBidTxGasLimit`).

### Impact Explanation
An unprivileged auction bidder who triggers a failure in `getBidTxGasLimit` after successful insertion becomes locked out of bidding for that target block for the remainder of its validity window (`[currentBlockNumber+1, currentBlockNumber+allowFutureBlock]`). This denies the bidder the ability to correct or improve their bid, effectively causing them to lose the auction for that block despite being otherwise eligible — a concrete denial-of-service / settlement-fairness issue for the auction module reachable by a single RPC call (`auction_submitBid`). Impact is scoped to Medium since it is bounded in time (until the target block passes) and requires a specific failure condition in gas-limit computation, but it does directly cause loss of bidding opportunity/auction settlement participation for the affected sender.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires `getBidTxGasLimit` to fail after `insertBid` has already succeeded, which depends on the auction entry point/target tx state at call time. Since bid submission is public (`auction_submitBid` RPC, reachable by any bidder) and `getBidTxGasLimit`'s success is not fully guaranteed to be aligned with the pre-checks done in `validateBid`, this is a reasonably reachable, permissionless failure path.

### Recommendation
Move all fallible pre-checks (including gas-limit computation) before calling `insertBid`, so that `insertBid` is only invoked once the bid is fully known-valid and ready to commit. Alternatively, in the error path after `insertBid` succeeds but a later step fails, explicitly revert the bid-pool state (remove the entry from `bidMap`, `bidTargetMap`, and `bidWinnerMap`) before returning the error, analogous to calling a `revertBid`/`removeBid` routine.

### Proof of Concept
1. Bidder A calls `auction_submitBid` with a validly signed bid for `blockNumber = N`.
2. `validateBid` passes; `insertBid` succeeds, registering A as the winner for block N (`bidWinnerMap[N][A] = hash`).
3. `getBidTxGasLimit(bid)` fails (e.g., due to a transient error in estimating gas for the auction entry point call) — `AddBid` returns an error to A, but the bid stays in `bidMap`/`bidTargetMap`/`bidWinnerMap`.
4. Bidder A resubmits a corrected or higher bid for block N (same or different target tx) — `validateBid` calls `senderHasDifferentWinner`, finds the stuck bid is not equal to the new one, and returns `ErrBidSenderExists`.
5. Bidder A is denied the ability to bid for block N until `removeOldBids` prunes the entry once the chain passes block N, causing A to lose that auction slot entirely. [1](#0-0) [5](#0-4)

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L141-156)
```go
func (bp *BidPool) removeOldBids(num uint64, txHashMap map[common.Hash]struct{}) {
	bp.bidMu.Lock()
	defer bp.bidMu.Unlock()

	// Remove the old bids.
	for bn := range bp.bidWinnerMap {
		if bn > num {
			continue
		}

		for _, bh := range bp.bidWinnerMap[bn] {
			delete(bp.bidMap, bh)
		}
		delete(bp.bidTargetMap, bn)
		delete(bp.bidWinnerMap, bn)
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

**File:** kaiax/auction/impl/bid_pool.go (L335-395)
```go
// senderHasDifferentWinner reports whether a different bid from the same sender
// already exists in the winner list for this block. Caller must hold bidMu.
func (bp *BidPool) senderHasDifferentWinner(bid *auction.Bid) bool {
	hash, ok := bp.bidWinnerMap[bid.BlockNumber][bid.Sender]
	if !ok {
		return false
	}
	return !bid.Equals(bp.bidMap[hash])
}

func (bp *BidPool) validateBid(bid *auction.Bid) error {
	blockNumber := bid.BlockNumber

	bp.bidMu.RLock()

	// Check if the auction tx is already in the pool.
	if _, ok := bp.bidMap[bid.Hash()]; ok {
		bp.bidMu.RUnlock()
		return auction.ErrBidAlreadyExists
	}

	// 1. The `bid.Sender` must not be in the winner list of the same block number if the new bid isn't equal to the previous bid.
	if bp.senderHasDifferentWinner(bid) {
		bp.bidMu.RUnlock()
		return auction.ErrBidSenderExists
	}
	bp.bidMu.RUnlock()

	curBlock := bp.Chain.CurrentBlock()
	if curBlock == nil {
		return auction.ErrBlockNotFound
	}

	// 2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
	curNum := curBlock.NumberU64()
	if blockNumber <= curNum || blockNumber > curNum+allowFutureBlock {
		return auction.ErrInvalidBlockNumber
	}

	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}

	// 4. The data size must be less than the maximum limit.
	if uint64(len(bid.Data)) > BidTxMaxDataSize {
		return auction.ErrExceedMaxDataSize
	}

	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}

	// 6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.
	if err := bp.validateBidSigs(bid); err != nil {
		return err
	}

	return nil
}
```
