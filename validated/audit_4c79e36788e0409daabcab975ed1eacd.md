### Title
Auction Bid Pool Inserts Bid Into Winner/Target Maps Before Gas-Limit Validation Succeeds, Leaving Unrecoverable Corrupted State on Failure - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.AddBid` mutates persistent bid-pool state (`bidMap`, `bidTargetMap`, `bidWinnerMap`) via `insertBid` **before** the bid has been fully validated by `getBidTxGasLimit`. If gas-limit computation fails after `insertBid` has already run, `AddBid` returns an error to the caller, but the bid remains permanently lodged in the pool's maps with no rollback path — mirroring the Snipe-IT pattern of performing an irreversible state-mutating step before validation completes, with no rollback mechanism on failure.

### Finding Description
`AddBid` performs the following ordered steps: [1](#0-0) 

1. `validateBid` — checks signatures, block-number range, bid amount, data size, call-gas-limit bound.
2. `insertBid` — **mutates** `bp.bidMap`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and `bp.bidWinnerMap[blockNumber][sender]`: [2](#0-1) 
3. `getBidTxGasLimit` — computes the gas limit by ABI-encoding the auction call data and computing intrinsic/floor gas, which can independently fail (e.g., `system.EncodeAuctionCallData` error, `types.IntrinsicGas` error, or `blockchain.FloorDataGas` error): [3](#0-2) 

If step 3 fails, `AddBid` returns the error immediately — but step 2 (`insertBid`) already committed the bid to all three maps. There is no compensating `removeBid`/rollback call on this error path. This means the bid pool ends up in a state where:
- `bp.bidWinnerMap[blockNumber][sender]` is now occupied by this "phantom", gas-limit-uninitialized bid.
- `bp.bidTargetMap[blockNumber][targetTxHash]` is likewise occupied.
- The bid's `GasLimit` field was never set via `bid.SetGasLimit(gasLimit)` (that line is never reached), so it retains its zero-value/uninitialized state, yet the bid is otherwise indistinguishable from a fully valid winning bid to any consumer of `GetTargetTxMap`.

Because `validateBid` and `insertBid` explicitly check `senderHasDifferentWinner` to reject bids from a sender that already has a *different* winning bid for that block, any subsequent legitimate bid (even a well-formed, fully valid one) from the same sender or targeting the same `targetTxHash` for that block will be rejected with `ErrBidSenderExists` / `ErrLowBid`, since the corrupted phantom bid is already recorded as the "winner" and can't be evicted through the normal replace-if-better logic without the sender resubmitting the exact same (broken) bid.

This directly parallels the Snipe-IT bug class: a destructive/state-changing operation (`insertBid`, analogous to the DB wipe) executes before the full validation chain (`getBidTxGasLimit`, analogous to the archive-integrity check) completes, and there is no rollback mechanism when the later validation step fails.

### Impact Explanation
This is reachable by any unprivileged/public auction bidder (searcher or the CN's `HandleBid` P2P path, or via the `auction_submitBid` RPC — `AuctionAPI.SubmitBid` → `bidPool.AddBid`): [4](#0-3) 

- **State divergence / auction settlement disruption**: A single crafted bid that passes `validateBid` (valid signatures, valid amount/size/gas bounds) but triggers a failure inside `getBidTxGasLimit` (e.g., malformed `Data`/`To`/`CallGasLimit` combination that breaks `EncodeAuctionCallData` or `IntrinsicGas`/`FloorDataGas` computation) permanently occupies that sender's and that target-tx's winner slot for the given block number, denying any subsequent, fully-valid, higher bid from being accepted for that block/target-tx pair.
- Because `GetTargetTxMap` is used by the block-building path to pull winning bids for bundling, a corrupted bid with an unset/zero gas limit being surfaced to the builder could also produce malformed `BidTx` generation or unintended fee/settlement mismatches when the auction module later tries to use this bid's gas limit for building a `BidTx` (this downstream consumption code path — `GetBidTxGenerator`/equivalent — could not be located in the indexed portion of the repo, so the exact downstream effect on settlement is not fully confirmed).
- At minimum, this is a state-consistency violation reachable by any public bidder with no privileges, causing the bid pool to diverge from its intended invariants and blocking legitimate auction participants — a Medium/High-severity availability/integrity issue in the auction settlement pipeline, directly analogous to the "no rollback after partial destructive operation" bug class in the report.

### Likelihood Explanation
Likely reachable: any external actor who can reach `auction_submitBid` RPC or send P2P bid messages (`HandleBid`) can submit a bid that passes the coarse checks in `validateBid` (signature checks, size/limit checks) yet fails at the finer-grained `getBidTxGasLimit` step. Whether `EncodeAuctionCallData`/`IntrinsicGas`/`FloorDataGas` can actually be forced to fail via attacker-controlled `bid.Data`, `bid.To`, or `bid.CallGasLimit` combinations could not be fully confirmed from the indexed code (the `system.EncodeAuctionCallData` and `types.IntrinsicGas` implementations were only partially visible), so the exact triggering condition is not 100% verified, but the missing rollback itself is a clear defect regardless of how easy it is to trigger the failing branch.

### Recommendation
Perform `getBidTxGasLimit` computation (and any other gas/encoding validation) **before** calling `insertBid`, so that no pool state mutation occurs until the bid is fully validated and ready to be committed. Alternatively, if `getBidTxGasLimit` fails after `insertBid`, explicitly roll back the insertion (delete from `bidMap`, `bidTargetMap`, `bidWinnerMap`) before returning the error.

### Proof of Concept
1. A searcher crafts a `Bid` with a valid `SearcherSig`/`AuctioneerSig`, valid `Bid` amount, `Data` size ≤ `BidTxMaxDataSize`, and `CallGasLimit` ≤ `BidTxMaxCallGasLimit` (so it passes `validateBid`), but with a `Data`/`To`/version combination crafted to make `system.EncodeAuctionCallData` (or the subsequent `types.IntrinsicGas`/`blockchain.FloorDataGas` call) return an error.
2. Submit this bid via `auction_submitBid` (`AuctionAPI.SubmitBid` → `BidPool.AddBid`).
3. `validateBid` passes; `insertBid` commits the bid into `bp.bidMap`, `bp.bidTargetMap[blockNumber][targetTxHash]`, and `bp.bidWinnerMap[blockNumber][sender]`.
4. `getBidTxGasLimit` fails; `AddBid` returns the error to the RPC caller, but the corrupted bid remains in all three maps (confirmed by code inspection at `kaiax/auction/impl/bid_pool.go:261-274`; no cleanup call exists on this error path).
5. A legitimate bidder (the same sender, or any bidder targeting the same `targetTxHash`) subsequently submits a fully valid, higher bid for the same block number; it is rejected with `ErrBidSenderExists`/`ErrLowBid` due to `senderHasDifferentWinner`/existing-target-map checks against the corrupted phantom bid, denying legitimate auction participation for that block.

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
