### Title
Auction bid pool leaks committed bid state on `getBidTxGasLimit` failure in `AddBid()` - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.AddBid()` performs a two-phase commit of an incoming bid: it first calls `insertBid(bid)`, which mutates the pool's core indices (`bidMap`, `bidTargetMap`, `bidWinnerMap`), and only afterwards calls `getBidTxGasLimit(bid)` to compute and finalize the bid's gas limit. If `getBidTxGasLimit` returns an error, `AddBid` returns the error to the caller without undoing the `insertBid` mutation, leaving a "half-committed" bid permanently resident in the pool's maps. [1](#0-0) 

### Finding Description
`AddBid` is the entry point reachable from the public `auction_submitBid` RPC method via `AuctionAPI.SubmitBid`, i.e. it is directly callable by any external caller (the `Auctioneer`, but the RPC endpoint itself does not perform additional authorization beyond bid signature checks inside `validateBid`). [2](#0-1) 

The sequence in `AddBid` is:
1. `validateBid(bid)` — pure validation, no state mutation.
2. `insertBid(bid)` — mutates `bp.bidMap[hash]`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and `bp.bidWinnerMap[blockNumber][sender]`.
3. `getBidTxGasLimit(bid)` — if this returns a non-nil error, `AddBid` returns immediately with that error. [3](#0-2) 

Because step 2's mutation is never rolled back when step 3 fails, the bid remains recorded as the current bid for `(blockNumber, targetTxHash)` in `bidTargetMap` and as the sender's active winning bid in `bidWinnerMap`, even though the API call reported failure to the caller. This is structurally the same bug class as the reported CVE: `mlx4_srq_alloc()` succeeds, a later step fails, and the missing `mlx4_srq_free()` call leaves the resource allocated/leaked despite the overall operation returning an error.

The leaked entry has two concrete downstream effects reachable by the auctioneer:
- `senderHasDifferentWinner(bid)`, called from both `validateBid` and `insertBid`, will treat the sender as already holding a winner slot for that block, causing subsequent legitimate bids from the same sender for that block to be rejected with `ErrBidSenderExists`, unless the new bid happens to be `Equals()` to the leaked (partially invalid) one. [4](#0-3) 
- The leaked bid stays discoverable through `GetTargetTxMap`, which is used by block-building code (`AuctionModule.FilterTxs`/builder path) to select winning bids for inclusion in blocks, meaning a bid whose gas-limit computation failed can still be picked up by block assembly logic. [5](#0-4) 

### Impact Explanation
This does not cause an immediate unauthorized value transfer, but it creates state divergence risk and availability abuse:
- A malicious or buggy `Auctioneer`/caller can intentionally submit a bid that is known to fail `getBidTxGasLimit` (e.g., referencing a target tx or entry-point state that causes the gas estimate call to fail) to occupy the sender's winner slot for a given block, denying that sender's ability to have further valid bids accepted for the block (griefing/availability impact within the auction module).
- A bid with an unresolved/invalid gas limit lingering in `bidTargetMap`/`GetTargetTxMap` could be picked up by block-building logic before its state is corrected, risking inconsistent bid processing between nodes if the failure is non-deterministic (e.g., depends on RPC/backend state at call time vs. block-building time), which could result in state divergence between honest nodes.

I was not able to fully inspect `getBidTxGasLimit`'s implementation (in `kaiax/auction/impl/getter.go`) before running out of tool calls, so I cannot confirm the exact conditions under which it fails or definitively prove a full value-movement exploit chain — this should be verified by a follow-up review of `getter.go`.

### Likelihood Explanation
Likelihood is Medium: reaching this path requires only a single `auction_submitBid` RPC call with a bid engineered to pass `validateBid` (block-number range, positive bid, size/gas-limit bounds, valid searcher/auctioneer signatures) but fail the later gas-limit computation. Since `validateBid` already checks most structural constraints, an attacker with a valid auctioneer signature (or an auctioneer key compromise) could reliably trigger this leak; the exact reachability of `getBidTxGasLimit` failure paths under attacker control is unconfirmed due to not having inspected `getter.go`.

### Recommendation
In `BidPool.AddBid()`, if `getBidTxGasLimit(bid)` returns an error after `insertBid(bid)` has succeeded, roll back the insertion by removing the entry from `bidMap`, `bidTargetMap[blockNumber]`, and `bidWinnerMap[blockNumber]` (mirroring the removal logic already present in `removeOldBids`) before returning the error, so that a failed `AddBid` call never leaves partial state in the pool. [3](#0-2) 

### Proof of Concept
1. Craft a bid `B` that satisfies all `validateBid` checks (correct future block number, positive `Bid`, data/gas-limit within bounds, valid `SearcherSig`/`AuctioneerSig`).
2. Ensure `B` triggers a failure in `getBidTxGasLimit` (e.g., by referencing conditions unverified by `validateBid` but consumed later by the gas-estimation call in `getter.go` — exact trigger unconfirmed pending review of that file).
3. Call `auction_submitBid` with `B`. Observe that `AddBid` returns a non-nil error to the RPC caller.
4. Immediately submit a second, fully valid bid `B2` from the same sender for the same block. Observe it is rejected with `ErrBidSenderExists` via `senderHasDifferentWinner`, because the failed bid `B` is still recorded in `bidWinnerMap[blockNumber][sender]` from the earlier leaked `insertBid` call. [1](#0-0)

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
