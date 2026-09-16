### Title
Bid validation TOCTOU allows stale-auctioneer or stale-config bids to be inserted into the live auction pool - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.AddBid` performs `validateBid` (which reads `bp.auctioneer`/`bp.auctionEntryPoint` under `auctionInfoMu.RLock()` inside `validateBidSigs`, and reads `bp.Chain.CurrentBlock()` with no lock at all) and then, *after releasing all locks*, calls `insertBid`, which re-acquires only `bidMu.Lock()` and re-checks solely `bidMap`/`senderHasDifferentWinner`. This mirrors the xfrm bug class: validity is established before a lock is taken, the protected state can be mutated ("killed"/rotated) in between, and the stale-validated item is still admitted into the trusted structure.

### Finding Description
`AddBid` → `validateBid` → `validateBidSigs` verifies `bid.SearcherSig`/`bid.AuctioneerSig` against the **currently configured** `auctioneer`/`auctionEntryPoint`/`auctionEntryPointVersion`, guarded only by `bp.auctionInfoMu.RLock()`: [1](#0-0) 

Separately, `execution.go` (block processing path, invoked by every new block) calls `updateAuctionInfo`, which — whenever the on-chain `SystemRegistry`/`AuctionEntryPoint` address changes — takes `auctionInfoMu.Lock()`, updates the auctioneer/entrypoint, and unconditionally calls `clearBidPool()` (which takes `bidMu.Lock()` and wipes `bidMap`, `bidTargetMap`, `bidWinnerMap`): [2](#0-1) [3](#0-2) 

The insertion path never re-validates the signature/auctioneer/entrypoint or the block-number window after re-acquiring the lock — it only re-checks for duplicate hash and `senderHasDifferentWinner`, exactly the narrow race the author was aware of per the comment "Re-check bidWinnerMap here — two concurrent bids can pass validateBid together": [4](#0-3) 

`AddBid` itself shows the full sequence: unlocked `validateBid` → unlocked gap (including `getBidTxGasLimit`) → `insertBid`: [5](#0-4) 

This is reachable by an unprivileged, external caller via the public JSON-RPC `auction_submitBid` endpoint: [6](#0-5) 

### Impact Explanation
If the auctioneer key or `AuctionEntryPoint` is rotated (a legitimate governance/system-contract operation processed at block boundaries), a bid whose signature was validated against the *old* auctioneer/entrypoint in `validateBidSigs` can still complete `insertBid` and land in the freshly-cleared pool that is supposed to only contain bids valid under the *new* configuration, because `insertBid`'s re-check does not include auctioneer/entrypoint/version or signature validity. A stale or previously-rejected bid (e.g., signed by a de-authorized auctioneer, or targeting the pre-rotation `AuctionEntryPoint`) could then be selected as the block's winning bid and injected into block building via `GetTargetTxMap`/`GetBidTxGenerator`, potentially executing attacker-controlled calldata against a stale entry point or bypassing the intended auctioneer authorization — a form of unauthorized fee/auction-settlement manipulation. This is a genuine state-divergence/authorization-bypass risk in a fee-earning, block-inclusion-affecting subsystem.

### Likelihood Explanation
Exploitation requires the race window between `validateBid` completing (crypto verification takes measurable time) and `insertBid`'s lock acquisition, aligned with an auctioneer/entrypoint rotation event, which is infrequent (governance-controlled) but deterministic and attacker-triggerable in timing since the attacker fully controls when they submit the RPC bid relative to observed on-chain config-change transactions/blocks. It is not a trivial single-shot exploit but is a real, reachable, unprivileged-triggerable TOCTOU rather than a theoretical one, given the explicit but incomplete double-check pattern already present in the code (`insertBid`'s partial re-validation confirms the authors recognized related concurrency hazards but did not close this specific gap).

### Recommendation
Re-validate signatures (or at minimum the `auctioneer`/`auctionEntryPoint`/`auctionEntryPointVersion` values used to validate the signatures) inside `insertBid` while holding `bidMu`/`auctionInfoMu`, or restructure `AddBid` to perform the entire validate-then-insert sequence under a single held lock (analogous to the xfrm fix of moving the validity check inside the lock), so that a config rotation cannot admit a bid validated against stale auction parameters.

### Proof of Concept
Conceptual sequence (requires triggering under real timing, not verified with a live run given tool limitations):
1. Attacker submits a valid bid via `auction_submitBid` signed by current `auctioneer` A for current `AuctionEntryPoint` E — `validateBid`/`validateBidSigs` pass, reading `auctioneer=A`, `entryPoint=E`.
2. Before `insertBid` acquires `bidMu.Lock()`, a block is processed that changes `SystemRegistry`'s `AuctionEntryPoint`/auctioneer to A'/E', triggering `updateAuctionInfo` → `clearBidPool()`.
3. `insertBid` then proceeds, only checking `bidMap`/`senderHasDifferentWinner` (both now empty due to the clear), and inserts the bid — despite it being signed by the now-invalid auctioneer A / stale entry point E — into the pool that is meant to hold only A'/E'-valid bids.

Note: this analysis is based on static code reading; I was not able to execute the codebase to confirm the actual race timing window in a live environment. A Devin session with repository/test execution access would be needed to build a concurrent reproduction test (similar in style to the existing `TestBidPool_ConcurrentAddBid_OneWinnerPerSender`) that interleaves `AddBid` with `updateAuctionInfo`.

### Citations

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
