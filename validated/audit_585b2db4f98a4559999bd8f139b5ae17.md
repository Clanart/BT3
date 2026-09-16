### Title
Auction "early deadline" (EDOffset) filter can be bypassed for the target transaction itself, allowing the auction-protected transaction to be included without its paired bid - (File: kaiax/auction/impl/builder.go)

### Summary
Kaia's auction module implements an "early-deadline" exclusion window (`EDOffset`) that is conceptually the same protective mechanism as Oval's `lockWindow()`: it is meant to reserve a transaction that is the target of an active auction bid so that it can only enter the block paired with its winning bid (analogous to the "unlock" transaction that must receive the freshest, not-yet-consumed data/opportunity). In `AuctionModule.FilterTxs` (`kaiax/auction/impl/builder.go`), any transaction submitted after `deadline = now - EDOffset` is normally stripped from the candidate pool for that address, *except* transactions whose hash is found in `targetTxHashMap`, which are explicitly let through (`continue`) regardless of how late they arrived. This exemption is required so `ExtractTxBundles` can later locate the target tx and wrap it together with the bid transaction into a bundle.

### Finding Description
`FilterTxs` (`kaiax/auction/impl/builder.go` lines 84-121) is the gatekeeper that would otherwise remove late-arriving transactions from being minable in the current block: [1](#0-0) 

The exemption for target transactions guarantees the target tx stays in the candidate set even though it arrived inside the "lock window" (the `EDOffset` period). The actual pairing of the target tx with its bid only happens afterward, in `ExtractTxBundles`: [2](#0-1) 

Critically, bundling in `ExtractTxBundles` can fail to attach the bid to the target tx for several reasons that are independent of `FilterTxs`'s decision to keep the naked target tx in the pool:
- `atomic.LoadUint32(&a.bidPool.running) == 0` (auction turned off between the two calls) causes the whole function to return no bundles, while `FilterTxs` already let the target tx through.
- `bidTargetMap` for the mining block can be empty or the specific hash missing if the bid was removed (e.g., `removeOldBids` in `execution.go`) between the two passes.
- The new bundle can be marked `isConflict` against `prevBundles`/other bundles and silently dropped (`continue`), leaving the target tx un-bundled with no fallback re-check.

In every one of these paths, the target transaction — which was deliberately exempted from the early-deadline filter specifically so that it would be shielded until the bid bundle is attached — ends up being minable "naked," i.e., without the paying bid transaction ahead of it. This is the exact analog of the Oval bug: a mechanism designed to guarantee an "unlock"/paired transaction gets exclusive first access to a value opportunity is undermined by a secondary code path (bundle-conflict/bid-removal/running-flag) that returns the protected value (the target tx execution) without going through the guarded path, letting anyone capture the value that the lock window was meant to reserve for the auction winner.

### Impact Explanation
If the target transaction is included without its bid, the value/opportunity the auction was designed to monetize (OEV-style, extracted via the auction bid) is captured for free by whichever party's transaction happens to execute the target tx, and the auctioneer/protocol/searcher loses the auction proceeds entirely for that block. This is a concrete instance of fee/auction-settlement value being lost/redirected, matching the "auction settlement theft" / "reward redirection" impact class explicitly listed as in-scope.

### Likelihood Explanation
This requires no privileged access — it can be triggered by ordinary conditions of the txpool/bid lifecycle (bid pool state toggling `running` around fork/auction-info updates, bid removal via `removeOldBids`, or a bundle conflict against another bundle) combined with a target transaction that was already broadcast and is sitting in the pool. Because `FilterTxs` and `ExtractTxBundles` are two separate passes executed at different times during block building, any timing/state change between them can produce this divergence, making it a realistically reachable condition rather than a purely theoretical one.

### Recommendation
Do not unconditionally exempt target transactions from the early-deadline filter in `FilterTxs`. Instead, re-validate at filter time (or immediately before final inclusion) that a bundle can still be successfully formed for that target tx (bid still present, `bidPool.running == 1`, no conflicts with already-selected bundles) before allowing it to bypass the deadline. Alternatively, add a final block-assembly-time check that rejects/removes any target-tx-matching transaction that made it into the block without an accompanying, correctly-ordered bid transaction, mirroring the recommendation from the original report to validate all guard parameters (`maxAge`/`lockWindow` equivalents) so the "protected" path can never be silently short-circuited by a fallback code path.

### Proof of Concept
1. A searcher submits `bidInput.TargetTxRaw` via `AuctionAPI.SubmitBid` (`kaiax/auction/impl/api.go`), which both sends the raw target tx to the pool and inserts a `Bid` referencing it into `bidPool` for block `N+1`. [3](#0-2) 
2. During block building for block `N+1`, `FilterTxs` runs first and finds the target tx hash in `targetTxHashMap`, so it is kept in the candidate list even though it was submitted within the `EDOffset` window. [4](#0-3) 
3. Before `ExtractTxBundles` runs, the bid is removed from `bidTargetMap` (e.g., `removeOldBids` after a reorg/previous block insert) or `bidPool.running` flips to 0 (e.g., auctioneer/auction-entry-point registry read fails in `updateAuctionInfo`), so `ExtractTxBundles` returns no bundle for that tx. [5](#0-4) 
4. The target transaction, already left in the pool by step 2, is then included directly in the block by ordinary block-assembly ordering, executing the MEV/arbitrage opportunity without any accompanying bid payment — reproducing the Oval-style loss of the "lock window" guarantee.

### Citations

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

**File:** kaiax/auction/impl/builder.go (L94-118)
```go
	targetTxHashMap := a.bidPool.getTargetTxHashMap(curBlock.NumberU64() + 1)
	edOffset := a.AuctionConfig.EDOffset

	now := time.Now()
	deadline := now.Add(-edOffset)
	// filter txs that are after the auction early deadline
	for addr, list := range txs {
		for i, tx := range list {
			if tx.Time().After(deadline) && !a.isGaslessTx(tx) {
				// if the tx is a target tx, skip it
				if _, ok := targetTxHashMap[tx.Hash()]; ok {
					continue
				}

				if i == 0 {
					// if first transaction exceeds deadline, remove the address
					delete(txs, addr)
				} else {
					// keep only transactions before the deadline
					txs[addr] = list[:i]
				}
				break
			}
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

**File:** kaiax/auction/impl/execution.go (L29-41)
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
```
