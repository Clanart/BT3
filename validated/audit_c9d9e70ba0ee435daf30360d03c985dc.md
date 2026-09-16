## Title
Orphaned auction bid on partial `AddBid` failure causes permanent auction-slot lockout — ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid` inserts a bid into the pool's authoritative maps (`bidMap`, `bidTargetMap`, `bidWinnerMap`) via `insertBid`, and only *after* that success calls `getBidTxGasLimit`, which can independently fail and cause `AddBid` to return an error to the caller. The already-inserted bid is never rolled back on this failure path, so the pool's internal state diverges from what the RPC caller believes happened.

### Finding Description
`AddBid` performs three sequential steps [1](#0-0) :
1. `validateBid` — pure validation, no state mutation.
2. `insertBid` — locks `bidMu` and writes the bid into `bp.bidMap`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and `bp.bidWinnerMap[blockNumber][sender]` [2](#0-1) .
3. `getBidTxGasLimit` — computed *after* the bid is already committed to the maps; it can return an error (from `system.EncodeAuctionCallData`, `types.IntrinsicGas`, or `blockchain.FloorDataGas`) [3](#0-2) .

If step 3 fails, `AddBid` returns `(common.Hash{}, err)` to the RPC layer without ever deleting the entry written in step 2. The caller (via `auction_submitBid`) is told the bid failed [4](#0-3) , yet the bid object remains alive inside `bp.bidWinnerMap[blockNumber][sender]` and `bp.bidTargetMap[blockNumber][targetTxHash]`.

This is directly analogous to the CVE-2020-1897 bug class: an object's lifetime (the bid) is not correctly synchronized with the caller-visible "handled/freed" state after an error occurs mid-way through a multi-step request-handling sequence, leading to inconsistent internal bookkeeping that persists beyond what the client protocol believes is the object's lifetime.

Consequences of the orphaned entry:
- `senderHasDifferentWinner` will treat the sender as already having a (different, phantom) winning bid for that block, rejecting *all* subsequent legitimate bids from that sender for the same block via `ErrBidSenderExists` in both `validateBid` and `insertBid` [5](#0-4) [6](#0-5) [7](#0-6) .
- `bp.bidTargetMap[blockNumber][targetTxHash]` will hold the orphaned bid, blocking any other searcher from bidding on the same target transaction unless their bid strictly outbids the phantom entry (`existingBid.Bid.Cmp(bid.Bid) >= 0` returns `ErrLowBid`) [8](#0-7) .
- The orphaned bid is only cleared when the block passes (`removeOldBids`) or replaced by a strictly higher bid — meaning within the current auction window the target transaction's auction slot is effectively unusable/griefed.

### Impact Explanation
Any unauthorized/unprivileged auction bidder reachable via the public `auction_submitBid` RPC can grief the auction mechanism for a specific block/target-tx by submitting a bid that passes `validateBid` and `insertBid` but is crafted to fail `getBidTxGasLimit` (e.g., via encoding edge cases in `bid.Data`/`bid.CallGasLimit` reaching `EncodeAuctionCallData`/`IntrinsicGas`). This locks out the sender's own future bids for that block and can block competing bids for the same target transaction, disrupting fee-auction settlement — a form of unauthorized state divergence/auction manipulation reachable from a single submitted RPC call.

### Likelihood Explanation
Reachable directly and repeatedly by any external caller of the public (if enabled) `auction` RPC namespace with no special privileges; the API is explicitly listed as `Public: false` [9](#0-8) , but is still reachable by any client permitted to call the auction namespace (bidders/searchers), which is the intended actor class for this feature. Exact conditions to make `getBidTxGasLimit` fail after `insertBid` succeeds (i.e., a bid payload valid enough to pass `validateBid` but that trips an error in `EncodeAuctionCallData`/`IntrinsicGas`/`FloorDataGas`) could not be fully confirmed from the available index content for `blockchain/system/auction.go` and `types.IntrinsicGas`; this needs further verification with full file access.

### Recommendation
Compute `getBidTxGasLimit` (and any other fallible derived data) *before* calling `insertBid`, or wrap `insertBid`/`getBidTxGasLimit` in a single transactional step that removes the just-inserted bid from all three maps if any subsequent step fails, ensuring the pool state is never left in a state where a bid is retained despite `AddBid` reporting failure to the caller.

### Proof of Concept
1. As an unprivileged bidder, call `auction_submitBid` with a validly-signed `BidInput` (passing `validateBid`, i.e., valid signatures, valid block-number window, positive bid, size/gas limits within bounds) whose `Data`/`CallGasLimit` combination causes `system.EncodeAuctionCallData` or `types.IntrinsicGas`/`blockchain.FloorDataGas` to return an error.
2. `AddBid` proceeds through `validateBid` (success) and `insertBid` (success, bid written into `bidMap`/`bidTargetMap`/`bidWinnerMap`), then fails at `getBidTxGasLimit`, returning an error to the RPC caller.
3. Submit a second, well-formed bid from the same sender for the same block: it is rejected with `ErrBidSenderExists` because `bidWinnerMap[blockNumber][sender]` still references the orphaned first bid.
4. Any other searcher bidding on the same `targetTxHash` for that block must beat the orphaned bid's price or receive `ErrLowBid`, even though the orphaned bid was reported as failed to its original submitter.

Note: full verification of exactly which `bid.Data`/`bid.CallGasLimit` inputs trigger a `getBidTxGasLimit` failure after passing `validateBid` requires reading `blockchain/system/auction.go` (`EncodeAuctionCallData`) and `types.IntrinsicGas` in full, which was not completely available in this session's index; a Devin session with full file access should confirm this precondition before treating the PoC as fully proven.

### Citations

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

**File:** kaiax/auction/impl/bid_pool.go (L290-292)
```go
	if bp.senderHasDifferentWinner(bid) {
		return auction.ErrBidSenderExists
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L295-299)
```go
	if existingBid, ok := bp.bidTargetMap[blockNumber][targetTxHash]; ok {
		// FCFS if the bid is the same.
		if existingBid.Bid.Cmp(bid.Bid) >= 0 {
			return auction.ErrLowBid
		}
```

**File:** kaiax/auction/impl/bid_pool.go (L311-323)
```go
	hash := bid.Hash()

	bp.initializeBidMap(blockNumber)

	bp.bidMap[hash] = bid
	bp.bidTargetMap[blockNumber][targetTxHash] = bid
	bp.bidWinnerMap[blockNumber][sender] = hash

	numBidsGauge.Update(int64(len(bp.bidMap)))

	logger.Trace("Add bid", "bid", hash)

	return nil
```

**File:** kaiax/auction/impl/bid_pool.go (L337-343)
```go
func (bp *BidPool) senderHasDifferentWinner(bid *auction.Bid) bool {
	hash, ok := bp.bidWinnerMap[bid.BlockNumber][bid.Sender]
	if !ok {
		return false
	}
	return !bid.Equals(bp.bidMap[hash])
}
```

**File:** kaiax/auction/impl/bid_pool.go (L356-360)
```go
	// 1. The `bid.Sender` must not be in the winner list of the same block number if the new bid isn't equal to the previous bid.
	if bp.senderHasDifferentWinner(bid) {
		bp.bidMu.RUnlock()
		return auction.ErrBidSenderExists
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

**File:** kaiax/auction/impl/api.go (L48-57)
```go
func (a *AuctionModule) APIs() []rpc.API {
	return []rpc.API{
		{
			Namespace: "auction",
			Version:   "1.0",
			Service:   newAuctionAPI(a),
			Public:    false,
		},
	}
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
