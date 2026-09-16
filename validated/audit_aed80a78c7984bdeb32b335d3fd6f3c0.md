Based on the evidence gathered, there's a plausible analog in the auction bid pool's `AddBid` flow, though I was unable to fully verify the downstream consumer of `bidWinnerMap`/`bidTargetMap` (i.e., where the pool's data feeds into block-building/settlement) due to a failed final read of the complete `bid_pool.go` file. I present the finding with that caveat.

### Title
Stale bid remains registered in the auction BidPool after `AddBid` returns an error on gas-limit computation failure - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.AddBid` inserts a bid into the pool's internal indices (`bidMap`, `bidTargetMap`, `bidWinnerMap`) via `insertBid` *before* the bid's gas limit is computed. If `getBidTxGasLimit` fails after a successful insert, `AddBid` returns the error to the caller, but the bid is never removed from the pool's maps.

### Finding Description
`AddBid` performs, in order: `validateBid` → `insertBid` → `getBidTxGasLimit` → `bp.newBidCh <- bid`. [1](#0-0) 

`insertBid` mutates shared pool state under `bidMu`: it writes to `bp.bidMap[hash]`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and `bp.bidWinnerMap[blockNumber][sender]`, and — critically — it also evicts/replaces any pre-existing bid for the same `(blockNumber, targetTxHash)` slot by deleting the old bid's map entries before installing the new one. [2](#0-1) 

After `insertBid` succeeds, `AddBid` calls `bp.getBidTxGasLimit(bid)`; if that call returns an error, `AddBid` returns `(common.Hash{}, err)` to the RPC caller without ever calling any removal/rollback logic for the just-inserted bid. [3](#0-2) 

This is structurally the same bug class as CVE-2022-48771: a resource is registered into a live, shared table (fd table / bid pool index) before the operation that can fail has completed, and the failure path does not undo the registration — leaving a "stale" but fully addressable/functional entry that the caller believes does not exist (because they received an error). Here, the caller (an unprivileged auction bidder, via the public `auction_submitBid` RPC) receives an error and has no indication that their bid — including having evicted a legitimate competing bid via the FCFS/higher-bid replacement logic in `insertBid` — is still live in `bp.bidMap`/`bp.bidTargetMap`/`bp.bidWinnerMap`. [4](#0-3) 

### Impact Explanation
Because `insertBid` both installs the new bid and evicts a previously-winning bid for the same target-tx slot in the same lock section, a bidder can submit a bid that: (1) passes `validateBid` (sig checks, block-number range, non-zero bid, size/gas caps) and out-bids the current winner in `insertBid`, thereby evicting the legitimate winning bid from `bidWinnerMap`/`bidTargetMap`; and (2) subsequently fails only in `getBidTxGasLimit` (a step downstream of insertion). The caller/RPC sees an error and no bid hash, so it looks like the submission failed cleanly, but the pool state is left with the attacker's bid still registered as the current "winner" for that block/target — and the original legitimate bid has been permanently removed with no requeue. If the auction/block-building logic consumes `bidWinnerMap`/`bidTargetMap` to select the winning bid for inclusion (as its naming and the presence of "winner" tracking implies), this can result in auction settlement using a bid that was never fully validated for gas-limit computation, denial of the legitimate winner's bid, or a self-inconsistent state between the RPC-reported outcome and the actual pool state. This maps to "gasless or auction settlement theft" / "state divergence" categories in scope.

### Likelihood Explanation
Any unprivileged auction bidder can trigger this by crafting a `BidInput` that passes `validateBid` but causes `getBidTxGasLimit` to fail (e.g., by targeting a transaction/gas-estimation path that errors after the bid has already displaced an existing winner). No special privileges are required — this is reachable via the public `auction_submitBid` RPC path. [4](#0-3) 

### Recommendation
In `BidPool.AddBid`, defer the call to `insertBid` until after `getBidTxGasLimit` has succeeded (mirroring the vmwgfx fix of deferring `fd_install` until after the usercopy succeeds), or, if insertion must happen first for locking/atomicity reasons, add an explicit rollback path that calls a `removeBid`/`insertBid`-inverse function to restore the previously evicted bid (or delete the newly inserted one) whenever any step after `insertBid` fails. [1](#0-0) 

### Proof of Concept
1. As an unprivileged bidder, call `auction_submitBid` with a `BidInput` whose `TargetTxRaw`/`TargetTxHash`/`Sender`/`Bid`/signatures all pass `validateBid` (correct EIP-712 searcher/auctioneer signatures, valid block number window, positive bid, size/gas within caps) and whose bid amount exceeds an existing bid for the same `(blockNumber, targetTxHash)` slot, so `insertBid` evicts the current winner and installs the new bid in `bidMap`/`bidTargetMap`/`bidWinnerMap`. [5](#0-4) 
2. Craft the bid such that `bp.getBidTxGasLimit(bid)` fails afterward (this depends on the exact implementation of gas-limit computation, which was not fully retrievable in this session — noted as unverified).
3. Observe that `AddBid` returns an error/empty hash to the RPC caller, while the previously-winning bid has already been deleted from `bp.bidMap`/`bp.bidWinnerMap[blockNumber]` and the failed bid remains inserted in those same maps, since no rollback occurs on the `getBidTxGasLimit` error path. [3](#0-2) 

**Uncertainty note:** I was not able to fully read the remainder of `kaiax/auction/impl/bid_pool.go` (specifically `getBidTxGasLimit`'s implementation and any bid-removal/winner-selection consumer functions) in this session due to a tool-call failure on the final iteration. Confirming the exact failure conditions of `getBidTxGasLimit` and verifying that `bidWinnerMap`/`bidTargetMap` are indeed consumed by block-assembly/auction-settlement logic (to confirm concrete value-movement impact rather than just pool-state corruption) requires further investigation before this can be treated as fully proven.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L250-274)
```go
// AddBid adds a bid to the bid pool.
// Required mutex is locked in each function.
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
