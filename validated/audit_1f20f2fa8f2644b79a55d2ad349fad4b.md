## Analysis

This finding's bug class — a privileged party changing an economic/parameter curve while user transactions are already pending, with no mechanism to protect users from stale assumptions — has a concrete analog in the `kaiax/auction` module (KIP-249).

### Root cause

The `AuctionEntryPoint` system contract exposes `changeGasParameters(_gasPerByteIntrinsic, _gasPerByteEip7623, _gasContractExecution, _gasBufferEstimate, _gasBufferUnmeasured)`, which lets the contract owner atomically change five gas-accounting parameters that determine how much gas/value is charged during bid execution [1](#0-0) .

However, the Kaia client only tracks **one** of these five parameters — `gasBufferEstimate` — to decide whether the bid pool state is stale:

`AuctionModule.updateAuctionInfo` reads only the auction entry point address, the auctioneer address, and `gasBufferEstimate` via `system.ReadGasBufferEstimate`, then forwards these to the bid pool: [2](#0-1) 

`BidPool.updateAuctionInfo` compares only `auctioneer`, `auctionEntryPoint`, `auctionEntryPointVersion`, and `bidTxGasBuffer` (i.e., `gasBufferEstimate`); if any of these change it clears the entire bid pool, discarding all pending bids: [3](#0-2) 

Separately, when a bid is admitted, `getBidTxGasLimit` computes the gas limit reserved for the bid transaction using only `bp.bidTxGasBuffer` (the tracked `gasBufferEstimate`) plus the EVM's own intrinsic gas calculation — it never references `gasPerByteIntrinsic`, `gasPerByteEip7623`, `gasContractExecution`, or `gasBufferUnmeasured`: [4](#0-3) 

### Title
Auction gas-parameter frontrunning: changes to `gasPerByteIntrinsic`/`gasPerByteEip7623`/`gasContractExecution`/`gasBufferUnmeasured` are not tracked, so pending searcher bids execute under stale assumptions - ([File: kaiax/auction/impl/execution.go], [File: kaiax/auction/impl/bid_pool.go])

### Summary
The `AuctionEntryPoint` contract owner can change five gas-accounting parameters via `changeGasParameters`, but the Kaia client's auction module (`kaiax/auction`) only monitors and reacts to changes in one of them (`gasBufferEstimate`). A searcher who has already signed and broadcast an `AuctionTx` bid (with a fixed `Bid` amount and `CallGasLimit`) has no protection if the owner changes `gasContractExecution`, `gasPerByteIntrinsic`, `gasPerByteEip7623`, or `gasBufferUnmeasured` before the bid is included in a block.

### Finding Description
`BidPool.updateAuctionInfo` only invalidates (clears) the entire bid pool when `auctioneer`, `auctionEntryPoint`, `auctionEntryPointVersion`, or `bidTxGasBuffer` (mapped from `gasBufferEstimate`) change [3](#0-2) . The other four gas parameters exposed by `changeGasParameters` are never read by the client at all — `updateAuctionInfo` in `execution.go` fetches only `ReadAuctioneer`, `ReadGasBufferEstimate`, and `ReadAuctionVersion` [5](#0-4) .

Consequently, a bid submitted and locked into the pool under one gas-cost regime (used both by `getBidTxGasLimit` to size its reserved block gas and, on-chain, by the contract's own `call()` execution accounting) remains in the pool unchanged when the owner alters `gasContractExecution`/`gasPerByteEip7623`/`gasPerByteIntrinsic`/`gasBufferUnmeasured`. This is directly analogous to the referenced report: an admin-controlled parameter set can change between a user's (searcher's) signed submission and its actual on-chain processing, and the affected party has no way to react, withdraw, or bound the outcome — there is no "minMintAmount"-style guard (the `MaxGasPrice` field in v3 bids only bounds gas price, not the contract's internal execution-gas accounting).

### Impact Explanation
Because the client-side gas reservation (`getBidTxGasLimit`) and the on-chain gas-cost accounting can diverge after a `changeGasParameters` call that the pool does not detect, a searcher's already-signed bid can be executed under materially different cost assumptions than what they agreed to when signing the `AuctionTx`, potentially causing the searcher to be charged from their deposit under updated parameters or their bid transaction to fail/behave unexpectedly at execution, without their consent to the new gas economics.

### Likelihood Explanation
The AuctionEntryPoint's owner (or governance controlling it) can call `changeGasParameters` at any time; searchers actively submit signed bids continuously and cannot preemptively react to an in-flight parameter change since the bid pool provides no signal or invalidation for four of the five tunable gas parameters.

### Recommendation
Track and react to changes in all five gas parameters (`gasPerByteIntrinsic`, `gasPerByteEip7623`, `gasContractExecution`, `gasBufferEstimate`, `gasBufferUnmeasured`) in `AuctionModule.updateAuctionInfo` / `BidPool.updateAuctionInfo`, clearing or re-validating pending bids whenever any of them change — not just `gasBufferEstimate`. Consider also allowing searchers to specify a maximum acceptable execution-gas-cost bound in their signed `AuctionTx`, analogous to a slippage/`minMintAmount` guard, so bids are rejected at execution time rather than silently processed under altered economics.

### Proof of Concept
1. Searcher signs and submits an `AuctionTx` bid targeting block `N`, computed and accepted into the bid pool under current `gasContractExecution`/`gasPerByteEip7623` values (`BidPool.AddBid` → `getBidTxGasLimit`, using only `bidTxGasBuffer`) [6](#0-5) .
2. Before block `N` is produced, the `AuctionEntryPoint` owner calls `changeGasParameters` and updates `gasContractExecution` (or `gasPerByteEip7623`/`gasPerByteIntrinsic`/`gasBufferUnmeasured`) to a new value [1](#0-0) .
3. `AuctionModule.PostInsertBlock` → `updateAuctionInfo` runs for the next block but only re-reads `gasBufferEstimate`; since that value is unchanged, `BidPool.updateAuctionInfo` sees no difference and does **not** clear the pool [7](#0-6) .
4. The searcher's stale bid remains in the pool and is bundled into block `N`, where it is executed on-chain against the newly changed gas-accounting parameters that the searcher never agreed to when signing.

### Citations

**File:** contracts/bindings/auction/Kip249.go (L556-561)
```go
// ChangeGasParameters is a paid mutator transaction binding the contract method 0x2a215610.
//
// Solidity: function changeGasParameters(uint256 _gasPerByteIntrinsic, uint256 _gasPerByteEip7623, uint256 _gasContractExecution, uint256 _gasBufferEstimate, uint256 _gasBufferUnmeasured) returns()
func (_IAuctionEntryPoint *IAuctionEntryPointTransactor) ChangeGasParameters(opts *bind.TransactOpts, _gasPerByteIntrinsic *big.Int, _gasPerByteEip7623 *big.Int, _gasContractExecution *big.Int, _gasBufferEstimate *big.Int, _gasBufferUnmeasured *big.Int) (*types.Transaction, error) {
	return _IAuctionEntryPoint.contract.Transact(opts, "changeGasParameters", _gasPerByteIntrinsic, _gasPerByteEip7623, _gasContractExecution, _gasBufferEstimate, _gasBufferUnmeasured)
}
```

**File:** kaiax/auction/impl/execution.go (L55-106)
```go
func (a *AuctionModule) updateAuctionInfo(num *big.Int) bool {
	auctioneer := common.Address{}
	auctionEntryPointAddr := common.Address{}
	auctionEntryPointVersion := ""
	bidTxGasBuffer := uint64(0)

	defer func() {
		a.bidPool.updateAuctionInfo(auctioneer, auctionEntryPointAddr, auctionEntryPointVersion, bidTxGasBuffer)
	}()

	header := a.Chain.GetHeaderByNumber(num.Uint64())
	if header == nil {
		return false
	}
	_, err := a.Chain.StateAt(header.Root)
	if err != nil {
		return false
	}

	backend := backends.NewBlockchainContractBackend(a.Chain, nil, nil)

	// 1. Read auction entry point address
	auctionEntryPointAddr, err = system.ReadActiveAddressFromRegistry(backend, system.AuctionEntryPointName, num)
	if err != nil {
		return false
	}

	if auctionEntryPointAddr == (common.Address{}) {
		return false
	}

	// 2. Read auctioneer address
	auctioneer, err = system.ReadAuctioneer(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	if auctioneer == (common.Address{}) {
		return false
	}

	// 3. Read gas buffer estimate
	bidTxGasBuffer, err = system.ReadGasBufferEstimate(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	// 4. Read AUCTION_VERSION to pick the right EIP-712 typehash and ABI for bids targeting this entry point.
	auctionEntryPointVersion, err = system.ReadAuctionVersion(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
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

**File:** kaiax/auction/impl/bid_pool.go (L250-269)
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
```

**File:** kaiax/auction/impl/bid_pool.go (L485-508)
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
```
