### Title
`AuctionAPI::SubmitBid` broadcasts the target transaction to the public mempool before the bid is validated/locked-in, enabling frontrunning of the auction opportunity - ([File: kaiax/auction/impl/api.go])

### Summary
`SubmitBid` sends the searcher's `targetTx` to the transaction pool (which propagates it to the whole network) *before* validating and registering the corresponding bid in the `BidPool`. This mirrors the reported `GladiusOrderQuoter::quote()` pattern, where the callee's intent is exposed to on-chain observers before the protective mechanism (execution/settlement) is finalized, letting third parties react and steal the opportunity.

### Finding Description
`AuctionAPI.SubmitBid` performs two sequential steps:

1. Decode `bidInput.TargetTxRaw` and immediately call `api.a.Backend.SendTx(ctx, targetTx)`, injecting the target transaction into the node's tx pool (and from there into the p2p mempool, visible to every peer/CN/searcher).
2. Only afterward does it build the `Bid` and call `api.a.bidPool.AddBid(bid)`, which performs signature verification, block-number range checks, duplicate/low-bid checks, and pool-size checks. [1](#0-0) 

This ordering means the target transaction — which encodes the profitable opportunity the searcher discovered (e.g., an arbitrage-triggering swap) — is broadcast and becomes visible to the entire network *before* the bid that is supposed to "reserve" the right to back-run it is confirmed as valid. `BidPool.AddBid`/`insertBid` can reject the bid for numerous reasons unrelated to the target tx itself (`ErrLowBid` when a competing bid with equal/higher amount already exists for that target, `ErrBidSenderExists`, `ErrBidPoolFull`, `ErrInvalidSearcherSig`, `ErrInvalidAuctioneerSig`, etc.): [2](#0-1) [3](#0-2) 

In every one of those failure cases, the target transaction has already been leaked to the network with no compensating bid attached, exactly the "leak details before actual execution/settlement" pattern described in the report.

### Impact Explanation
Once `targetTx` sits in the mempool, any other searcher/bidder monitoring pending transactions can recognize the same MEV opportunity and submit a competing `auction_submitBid` call referencing the *same* `targetTxHash`. Because `insertBid` is a simple "replace if higher" comparison keyed by `(blockNumber, targetTxHash)`, a faster or better-informed competitor can win the auction for a target transaction that the original searcher discovered and paid the cost of revealing. In the worst case (the original bid is rejected for any of the reasons above, e.g., a race where another bid for the same target was already accepted), the target transaction is fully public and un-auctioned, and any watcher can claim the backrun value essentially for free by submitting a minimal winning bid. This is a direct value-extraction/reward-redirection vector reachable by any unprivileged auction bidder able to call the RPC.

### Likelihood Explanation
This is not an edge case — the ordering (`SendTx` before `AddBid`) is the mandatory code path executed on *every* `auction_submitBid` call. Given the auction module exists specifically to serve MEV searchers who actively race for backrun opportunities, competing searchers/bots monitoring the mempool are the expected and realistic threat model for this module, making exploitation highly likely whenever multiple searchers compete for the same or similar opportunities.

### Recommendation
Reverse or atomically couple the operations: validate and register the bid in `BidPool` first (or hold the target transaction back), and only broadcast/admit `targetTx` to the pool once the bid has been successfully validated and locked in as the winner for that `(blockNumber, targetTxHash)` key. Alternatively, perform bid validation and target-tx admission under a single lock so that a competing bid cannot be submitted against a target tx whose original bid has not yet been finalized, and ensure a rejected bid does not leave the target transaction independently exploitable in the pool.

### Proof of Concept
1. Searcher A calls `auction_submitBid` with a profitable `targetTxRaw` and `bid = X`. `SubmitBid` immediately calls `SendTx`, and the target transaction propagates to the mempool/peers (`kaiax/auction/impl/api.go` lines 124-136).
2. Before/while `AddBid(bid)` executes, an observing searcher B sees `targetTx` in the mempool, extracts the opportunity, and calls `auction_submitBid` with the same `targetTxHash` and a higher `bid = X+1`.
3. Due to the FCFS/"replace if higher" logic in `insertBid` (`kaiax/auction/impl/bid_pool.go` lines 294-309), searcher B's bid wins the target transaction that A revealed, or — if A's own bid is rejected for any validation reason — the target tx remains in the pool with no attached bid, letting B (or anyone) subsequently bid on it essentially uncontested, capturing the value A exposed.

### Citations

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

**File:** kaiax/auction/impl/bid_pool.go (L345-395)
```go
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
