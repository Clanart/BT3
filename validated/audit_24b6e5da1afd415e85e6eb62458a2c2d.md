## Analysis

The Story Protocol `DisputeModule` bug class is: **a single global slot/mapping keyed by attacker-influenceable data (evidence hash) is claimed by a throwaway submission from an unprivileged party, permanently or effectively locking out the legitimate submitter for that key.**

The closest reachable analog in this codebase is in the Kaia **auction module**'s `BidPool`, which is reachable by any unprivileged bidder via the public `auction_submitBid` RPC.

### Title
Single-Winner-Slot-Per-Target-Tx in Auction BidPool Allows a Cheap Equal/Lower Bid to Permanently Squat and Block a Legitimate Searcher's Bid for that Block Window - (File: `kaiax/auction/impl/bid_pool.go`)

### Summary
`BidPool.insertBid` enforces a single winning bid per `(blockNumber, targetTxHash)` key in `bidTargetMap`, and only replaces the incumbent bid if the challenger's bid is *strictly greater* (`existingBid.Bid.Cmp(bid.Bid) >= 0` → `ErrLowBid`) [1](#0-0) . Because bids are submitted directly to each node's bid pool via the public `auction_submitBid` RPC rather than going through a priced mempool, any unprivileged caller who can predict or observe a `targetTxHash` (a transaction that is public in the mempool once broadcast) can claim that slot first with a minimal bid, and any subsequent bid of the same or lower amount from the legitimate searcher is permanently rejected with `ErrLowBid` for that block/target pair.

### Finding Description
`insertBid` performs the slot check and tie-break logic: [2](#0-1) 

The rule from the module's own README states the same design intent: "if same block number, same target tx hash exists, replace it if it's better," with ties resolved first-come-first-served [3](#0-2) .

Since the `SubmitBid` RPC handler broadcasts the caller-supplied `TargetTxRaw` to the tx pool before registering the bid [4](#0-3) , and the target's hash is derivable by anyone (it's simply the hash of a transaction that will appear in the mempool), a malicious actor can:
1. Observe or predict the `targetTxHash` a legitimate searcher intends to bid on.
2. Submit their own bid for that exact `(blockNumber, targetTxHash)` pair with a minimal non-zero `Bid` value before the legitimate searcher's bid lands.
3. Because of the strict `>=` comparison, any subsequent bid equal to (or lower than) the squatter's minimal bid is rejected with `ErrLowBid`, and the legitimate searcher's expected auction slot for that block is denied unless they resubmit at a higher price — effectively a bidding-slot squat analogous to the evidence-hash squat in the reported bug, since the "used" state (`bidTargetMap[blockNumber][targetTxHash]`) is keyed on attacker-observable data rather than being scoped per-sender.

This mirrors the reported root cause: a **global, cross-user key derived from externally observable data gates a legitimate action**, and an unprivileged party can occupy that key first with a cheap, throwaway submission.

### Impact Explanation
A successful slot-squat forces the legitimate searcher to either lose their auction slot for that specific target transaction/block (their MEV opportunity is captured or blocked) or to overbid, directly resulting in fee/auction-settlement value diversion for that block. Since bids drive which `BidTx` bundle executes immediately after the target transaction (per KIP-249), this can redirect auction proceeds/settlement outcomes away from the rightful searcher for the affected block, which the rules classify as a valid "auction settlement" impact.

### Likelihood Explanation
Exploitation requires only the ability to call the public `auction_submitBid` RPC and to know/predict a `targetTxHash`, which is realistic since target transactions are broadcast to the public mempool before being bid on. The `allowFutureBlock` window (`currentBlockNumber+1` to `currentBlockNumber+2`) [5](#0-4)  gives an attacker a narrow but nonzero window to race a minimal bid ahead of the legitimate one for a given block.

### Recommendation
Scope the winning-slot key to include the bidding `Sender`/`Auctioneer`-approved identity in addition to `(blockNumber, targetTxHash)`, or require an increasing minimum bid increment plus tie-break by earliest strictly-higher offer rather than blocking all equal/lower future bids outright. Consider also validating that the `Sender` of the bid is authorized/known to the `Auctioneer` before allowing a slot claim, since the design already relies on `AuctioneerSig` for legitimacy — tightening this check would reduce the value of squatting with throwaway bids.

### Proof of Concept
1. Attacker monitors the mempool for transaction `T` with hash `targetTxHash` that a known/likely searcher will bid against for block `N`.
2. Attacker calls `auction_submitBid` with `{TargetTxHash: targetTxHash, BlockNumber: N, Bid: 1}` and a validly signed (self-issued via any account) bid, which is accepted by `insertBid` and occupies `bidTargetMap[N][targetTxHash]` [6](#0-5) .
3. Legitimate searcher submits a bid of the same or lower value for the same `(N, targetTxHash)`; `insertBid` returns `ErrLowBid` [7](#0-6) , and the legitimate searcher's `BidTx` is not scheduled for block `N`, unless they resubmit at a strictly higher price, incurring cost/delay caused entirely by the attacker's minimal throwaway bid.

### Citations

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

**File:** kaiax/auction/impl/bid_pool.go (L368-372)
```go
	// 2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
	curNum := curBlock.NumberU64()
	if blockNumber <= curNum || blockNumber > curNum+allowFutureBlock {
		return auction.ErrInvalidBlockNumber
	}
```

**File:** kaiax/auction/README.md (L13-24)
```markdown
## Bid pool validation rules

A bid pool is responsible for managing the valid bids from the `Auctioneer`. The bid must satisfy the following rules:

1. The `bid.Sender` must not be in the winner list of the same block number if the new bid doesn't have the same target block and hash as the previous bid.
2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
3. The `bid.Bid` must be greater than 0.
4. The `bid.Data` size must be less than or equal to `BidTxMaxDataSize`.
5. The `bid.CallGasLimit` must be less or equal to `BidTxMaxCallGasLimit`.
6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.

Please note that `Auctioneer` also validates the searcher's bid according to the KIP-249.
```

**File:** kaiax/auction/impl/api.go (L118-141)
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
```
