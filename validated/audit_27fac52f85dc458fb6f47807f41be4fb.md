### Title
Unsynchronized read of `auctionEntryPointVersion` in `BidPool.getBidTxGasLimit` races with concurrent `updateAuctionInfo` writes - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool` protects `auctioneer`, `auctionEntryPoint`, `auctionEntryPointVersion`, and `bidTxGasBuffer` with `auctionInfoMu` [1](#0-0) . Every other accessor takes the lock for the whole critical section, e.g. `GetAuctionEntryPointVersion` [2](#0-1)  and `validateBidSigs` [3](#0-2) . However, `getBidTxGasLimit` releases the lock after reading `bidTxGasBuffer` and then reads `bp.auctionEntryPointVersion` completely unlocked: [4](#0-3) 

Meanwhile, `updateAuctionInfo` (called once per block from `AuctionModule.PostInsertBlock` -> `updateAuctionInfo`) writes `auctionEntryPointVersion` under `auctionInfoMu.Lock()`: [5](#0-4) [6](#0-5) 

### Finding Description
This is the same bug class as the Celo `announceRunning` race: a field is properly mutex-guarded on the write side and on most read sites, but one read path escapes the lock. Here, `bp.auctionEntryPointVersion` (a Go `string`, whose header is a pointer+length pair and is not read/written atomically) is read outside `auctionInfoMu` in `getBidTxGasLimit` while `updateAuctionInfo` can concurrently overwrite it from the block-processing goroutine on every new block (`PostInsertBlock` runs per block insertion) [7](#0-6) .

`getBidTxGasLimit` is invoked from `AddBid`, which is reachable directly by an unprivileged, unauthenticated caller through the public `auction_submitBid` RPC endpoint: [8](#0-7) [9](#0-8) 

`AddBid` is also called from the `handleBidMsg` background goroutine for bids relayed by peers [10](#0-9) , so multiple concurrent callers (RPC handler goroutines + the message-handling goroutine) can call `getBidTxGasLimit` concurrently with a block-boundary `updateAuctionInfo` write, at exactly the moment the auction entry point contract's version changes.

Because `EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)` at line 490 consumes the racily-read string, a torn read of the string header can yield a corrupted length/pointer combination (in the worst case a wrong-version string, or in Go's memory model, undefined behavior), producing a `BidTx` calldata encoded with a mismatched/garbage `AUCTION_VERSION` selection. This can silently pick the wrong ABI/EIP-712 encoding path for `EncodeAuctionCallData`, causing the resulting bid transaction (built for on-chain execution against `AuctionEntryPoint`) to be malformed relative to the currently active contract version.

### Impact Explanation
An auction bid whose call data is generated against the wrong auction contract version can be included on-chain with incorrect gas-limit computation or malformed calldata, potentially causing the bid transaction to revert on some nodes but succeed on others depending on scheduling/timing of the race, i.e., state-transition divergence between honest nodes processing the same bid at slightly different times relative to the version rotation. In the narrower case it degrades to an inconsistent (too low/too high) intrinsic gas computation for the generated `BidTx`, which is used directly in block building (`FilterTxs`/bundle generation per the auction README) [11](#0-10) , risking gas-accounting or bid-settlement anomalies at exactly the auction-version transition boundary.

### Likelihood Explanation
The race is only "live" for the short window when the on-chain `AUCTION_VERSION` (or auctioneer/entry point) actually changes between blocks — this is an operator/governance-controlled and infrequent event, and every regular `getTargetTxHashMap`/`validateBidSigs`/`GetAuctionEntryPointVersion` read path is correctly locked, only `getBidTxGasLimit` is not. This narrows the practical window substantially, making the likelihood low-to-moderate rather than trivially always exploitable, but it is reachable purely via unprivileged, permissionless `auction_submitBid` RPC calls timed around a version update, with no privileged or p2p/consensus access required.

### Recommendation
Hold `auctionInfoMu.RLock()` for the entire duration of reads of `auctionEntryPointVersion` (and any other guarded fields) in `getBidTxGasLimit`, mirroring the pattern already used in `validateBidSigs`:

```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
    bp.auctionInfoMu.RLock()
    buffer := bp.bidTxGasBuffer
    version := bp.auctionEntryPointVersion
    bp.auctionInfoMu.RUnlock()

    data, err := system.EncodeAuctionCallData(bid, version)
    ...
}
```

### Proof of Concept
1. Node is running with an active auction module; `PostInsertBlock` is invoked per block via the normal block-insertion path, calling `updateAuctionInfo` which, under `auctionInfoMu.Lock()`, mutates `bp.auctionEntryPointVersion` whenever `system.ReadAuctionVersion` returns a new value (e.g., after a governance-driven contract upgrade of `AuctionEntryPoint`).
2. Concurrently, an external caller repeatedly invokes the public `auction_submitBid` RPC (`AuctionAPI.SubmitBid` -> `bidPool.AddBid` -> `getBidTxGasLimit`) around the block boundary where the version changes.
3. Running the node with Go's race detector (`-race`) during this scenario reproduces a WARNING: DATA RACE between the write in `updateAuctionInfo` (bid_pool.go:209) and the unlocked read in `getBidTxGasLimit` (bid_pool.go:490), analogous to the `announceRunning` race in the referenced Celo commit.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L54-58)
```go
	auctionInfoMu            sync.RWMutex
	auctioneer               common.Address
	auctionEntryPoint        common.Address
	auctionEntryPointVersion string
	bidTxGasBuffer           uint64
```

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

**File:** kaiax/auction/impl/bid_pool.go (L234-238)
```go
func (bp *BidPool) GetAuctionEntryPointVersion() string {
	bp.auctionInfoMu.RLock()
	defer bp.auctionInfoMu.RUnlock()
	return bp.auctionEntryPointVersion
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

**File:** kaiax/auction/impl/bid_pool.go (L457-469)
```go
func (bp *BidPool) handleBidMsg() {
	defer bp.wg.Done()

	for {
		select {
		case bid, ok := <-bp.bidMsgCh:
			if !ok {
				return
			}
			bp.AddBid(bid)
		}
	}
}
```

**File:** kaiax/auction/impl/bid_pool.go (L485-494)
```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	bp.auctionInfoMu.RLock()
	buffer := bp.bidTxGasBuffer
	bp.auctionInfoMu.RUnlock()

	data, err := system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)
	if err != nil {
		return 0, err
	}

```

**File:** kaiax/auction/impl/execution.go (L29-50)
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

	txHashMap := make(map[common.Hash]struct{})
	for _, tx := range block.Transactions() {
		txHashMap[tx.Hash()] = struct{}{}
	}
	a.bidPool.removeOldBids(block.Number().Uint64(), txHashMap)

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

**File:** kaiax/auction/impl/builder.go (L121-131)
```go
}

func (a *AuctionModule) isGaslessTx(tx *types.Transaction) bool {
	if a.gaslessModule == nil {
		return false
	}

	return a.gaslessModule.IsBundleTx(tx)
}


```
