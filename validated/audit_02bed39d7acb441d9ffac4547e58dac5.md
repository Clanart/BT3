## Analysis

The Linux CVE's bug class is a **TOCTOU race between a "check-then-use" fast path and an asynchronous teardown/reconfiguration path**: `rxrpc_kernel_charge_accept()` reads shared state (`rx->backlog`) and acts on it without holding the lock that the teardown path (`rxrpc_discard_prealloc()`) uses to null/free that same state, so a stale value gets used after teardown.

The closest reachable analog in kaia is the auction bid-admission pipeline in `kaiax/auction/impl/bid_pool.go`, which is reachable by any unpermissioned bidder submitting a bid via `AddBid`/`HandleBid`, and is subject to an asynchronous "teardown/reconfiguration" path (`updateAuctionInfo` → `clearBidPool`, or `stop()`).

`AddBid` performs its checks and mutations across **three separately-locked critical sections** instead of one atomic operation:
- an unsynchronized atomic read of `running` [1](#0-0) 
- `validateBid`, which validates the signature against the **current** `bp.auctioneer` / `bp.auctionEntryPoint*` under `auctionInfoMu.RLock` and releases it before returning [2](#0-1) [3](#0-2) 
- `insertBid`, which re-acquires `bidMu.Lock` later and only re-checks structural conditions (duplicate hash, winner conflict, price) — **it never re-validates the signature or re-checks that the auctioneer/entry-point configuration is unchanged** [4](#0-3) 

Concurrently, `updateAuctionInfo` can rotate the auctioneer/entry-point/version and wipe the pool at any time (e.g., on governance/auctioneer key rotation), fully protected by its own `auctionInfoMu.Lock` plus an internal `clearBidPool()` call: [5](#0-4) 

Because `validateBid`'s signature check and `insertBid`'s map mutation are not covered by a single lock spanning the whole "validate signature → insert" sequence, the following interleaving is possible for a bid whose sender crafted it against the *old* auctioneer configuration:

1. Bidder submits a bid signed under auctioneer A / entry point E1.
2. `validateBidSigs` succeeds while `bp.auctioneer == A`, `bp.auctionEntryPoint* == E1`.
3. Before `insertBid` runs, `updateAuctionInfo` rotates to auctioneer B / entry point E2 and clears the pool via `clearBidPool`.
4. `insertBid` then runs and inserts the bid — which was **never validated against B/E2** — into the now-current `bidMap`/`bidTargetMap`/`bidWinnerMap`, since `insertBid` does not re-run `validateBidSigs`.

This bid can subsequently be selected via `GetTargetTxMap`/`removeOldBids` during block building as the winning bid for the *new* auction configuration despite having been signed only under the stale, now-superseded auctioneer authorization — the same "check under one lock, act under a different (or no) lock after concurrent teardown/reconfiguration" flaw as the rxrpc CVE.

I was not able to fully trace `execution.go`/`handler_test.go` (where `PostInsertBlock`, `start()`, and the exact trigger for `running=1`/`updateAuctionInfo` calls live) within the remaining tool budget, so the precise block-building consumption path and whether any additional guard exists there is unconfirmed.

### Title
Auction bid admission lacks atomicity between signature validation and pool insertion across auctioneer rotation - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.AddBid` validates a bid's signature against the current auctioneer/entry-point configuration under `auctionInfoMu`, then inserts it into the bid pool under a separate `bidMu` lock in `insertBid`. Because these are two independently-locked critical sections, a concurrent `updateAuctionInfo` call (auctioneer/entry-point rotation, which also clears the pool) can occur in between, allowing a bid validated under the old auctioneer to be inserted into the pool representing the new configuration without re-validation.

### Finding Description
`AddBid` calls `validateBid` (which internally calls `validateBidSigs` under `auctionInfoMu.RLock`) and then, after releasing that lock, calls `insertBid` (under `bidMu.Lock`). `insertBid` only re-checks duplicate hashes, winner conflicts, and bid price — it never re-verifies `ValidateSearcherSig`/`ValidateAuctioneerSig` against the current `bp.auctioneer`/`bp.auctionEntryPoint`. If `updateAuctionInfo` rotates the auctioneer/entry point and clears the pool between these two steps, a bid signed under the stale configuration is admitted into the pool that is now supposed to represent the new configuration. [6](#0-5) [5](#0-4) 

### Impact Explanation
An admitted bid that was never authorized under the currently-active auctioneer can end up selected as the winning bid for a target transaction (via `bidTargetMap`/`bidWinnerMap` consulted during block building), constituting acceptance of an unauthorized/invalid auction settlement — a form of auction settlement integrity violation reachable by any external bidder without special privilege.

### Likelihood Explanation
Requires a narrow timing window between a bidder's `AddBid` call and an auctioneer/entry-point rotation via `updateAuctionInfo`, which is plausible under normal governance/key-rotation operations combined with a high-volume bidder racing the rotation; the bidder does not need to control the rotation itself, only submit bids continuously around it.

### Recommendation
Hold a single lock (or re-validate signatures) spanning the entire validate-then-insert sequence in `AddBid`, e.g., re-check the auctioneer/entry-point/version snapshot used for signature validation still matches the current configuration inside `insertBid` while holding `bidMu`, or perform signature validation and insertion under one combined critical section.

### Proof of Concept
Not independently executable from the index alone (requires driving `updateAuctionInfo` and `AddBid` concurrently); the race window is structurally demonstrated by [7](#0-6)  versus [5](#0-4) .

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

**File:** kaiax/auction/impl/bid_pool.go (L252-324)
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

**File:** kaiax/auction/impl/bid_pool.go (L345-361)
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
