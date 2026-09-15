Found it: `ReadGasBufferEstimate` in `blockchain/system/auction.go` reads `gasBufferEstimate` from the auction entry-point contract as an arbitrary-size `*big.Int` and truncates it with `buffer.Uint64()` with **no bounds/overflow check**. That truncated `bidTxGasBuffer` then feeds directly into an **unchecked `uint64` addition** in `getBidTxGasLimit`:

```go
// kaiax/auction/impl/bid_pool.go:485-509
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	...
	return max(intrinsicGas+bid.CallGasLimit+buffer, floorDataGas), nil
}
``` [1](#0-0) 

`buffer.Uint64()` performs a silent modulo-2^64 truncation of an arbitrary `uint256` value read from the (governance/auctioneer-controlled) `AuctionEntryPoint` contract:

```go
// blockchain/system/auction.go:51-62
func ReadGasBufferEstimate(backend bind.ContractCaller, contractAddr common.Address, num *big.Int) (uint64, error) {
	...
	buffer, err := caller.GasBufferEstimate(opts)
	...
	return buffer.Uint64(), nil
}
``` [2](#0-1) 

### Title
Unchecked `uint64` truncation/addition in auction bid gas-limit computation - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.getBidTxGasLimit` computes the gas limit for a bid-execution transaction as `intrinsicGas + bid.CallGasLimit + buffer` using unchecked Go `uint64` addition, and `buffer` itself is derived by silently truncating an unbounded on-chain `uint256` (`gasBufferEstimate()`) to `uint64` via `big.Int.Uint64()`, which wraps modulo 2^64 instead of erroring.

### Finding Description
`ReadGasBufferEstimate` fetches `gasBufferEstimate` from the `AuctionEntryPoint` system contract as a `*big.Int` and converts it with `.Uint64()` [3](#0-2) . Go's `big.Int.Uint64()` does not return an error for out-of-range values — it returns the low 64 bits, i.e., silent wraparound. This value flows unchanged into `bp.bidTxGasBuffer` via `updateAuctionInfo` [4](#0-3) , and is then summed with `intrinsicGas` and the bidder-supplied `bid.CallGasLimit` using plain `+` (no `math.SafeAdd`) in `getBidTxGasLimit` [1](#0-0) . Every other numeric aggregation for gas accounting in this codebase (EVM gas metering, `IntrinsicGasPayload`, `FloorDataGas`, `GasPool.AddGas`) explicitly uses `math.SafeAdd`/`SafeMul` or manual overflow checks [5](#0-4) [6](#0-5) [7](#0-6) , confirming this addition is an outlier lacking the SafeMath-style safeguard the external report calls out.

`bid.CallGasLimit` is separately bounded by `BidTxMaxCallGasLimit` in `validateBid` [8](#0-7) , so under the currently deployed contract this specific path is unlikely to overflow in practice — the real defect is the missing overflow check combined with the unchecked `Uint64()` truncation of a contract-controlled value, which is a latent correctness issue rather than a demonstrated exploitable path in the current deployment.

### Impact Explanation
If `gasBufferEstimate()` ever returns (or is set/upgraded to return) a value ≥ 2^64, or if `intrinsicGas + CallGasLimit + buffer` wraps around 2^64, `getBidTxGasLimit` could return an artificially small gas limit for the generated bid-execution transaction (`GetBidTxGenerator`), causing the transaction to run with insufficient gas (leading to out-of-gas reverts of legitimate auction settlement) or, in a worst case with crafted values, an artificially large/incorrect gas limit that bypasses expected bounds when constructing the settlement transaction included in a block. This affects auction settlement transaction correctness, which sits within the in-scope "gasless and auction modules" and "block assembly" surfaces.

### Likelihood Explanation
Low-to-medium. `bid.CallGasLimit` is capped by `BidTxMaxCallGasLimit`, and `gasBufferEstimate` is normally a small constant set by the auctioneer/contract owner rather than an unprivileged attacker, so triggering the overflow requires either a misconfigured/malicious `AuctionEntryPoint` contract owner or a future ABI/version change that allows unconstrained gas-buffer values. It is not directly triggerable by an unprivileged bidder alone under the current constants, which reduces confidence in immediate exploitability but does not eliminate the missing-safeguard defect.

### Recommendation
Use `common/math.SafeAdd` (already used throughout `blockchain/vm/gas_table.go` and `blockchain/types/tx_internal_data.go`) for the `intrinsicGas + bid.CallGasLimit + buffer` computation in `getBidTxGasLimit`, and make `ReadGasBufferEstimate` reject or clamp values that do not fit in `uint64` instead of silently truncating via `big.Int.Uint64()`.

### Proof of Concept
1. Deploy/upgrade an `AuctionEntryPoint` contract (or point the registry to one) whose `gasBufferEstimate()` returns a `uint256` value such that `(value mod 2^64) + intrinsicGas + BidTxMaxCallGasLimit` wraps past `math.MaxUint64`.
2. Trigger `updateAuctionInfo` (runs on every `PostInsertBlock` once Randao fork and auction are active) so `bp.bidTxGasBuffer` is updated with the truncated value [4](#0-3) .
3. Submit a bid near `BidTxMaxCallGasLimit` via `AddBid`/RPC; `getBidTxGasLimit` computes a wrapped/incorrect gas limit that is used to build and sign the on-chain bid-settlement transaction [9](#0-8) , producing a transaction with an unintended gas limit.

**Note:** I could not fully verify the exact numeric value of `BidTxMaxCallGasLimit`/`BidTxMaxDataSize` constants (the index returned only match locations, not the constant values), so I cannot confirm whether the current hard-coded cap makes the wraparound arithmetically reachable today without a change to the on-chain `gasBufferEstimate` value — this is stated as an open uncertainty rather than a confirmed exploit.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L384-387)
```go
	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
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

**File:** blockchain/system/auction.go (L51-61)
```go
func ReadGasBufferEstimate(backend bind.ContractCaller, contractAddr common.Address, num *big.Int) (uint64, error) {
	caller, err := contracts.NewIAuctionEntryPointCaller(contractAddr, backend)
	if err != nil {
		return 0, err
	}
	opts := &bind.CallOpts{BlockNumber: num}
	buffer, err := caller.GasBufferEstimate(opts)
	if err != nil {
		return 0, err
	}
	return buffer.Uint64(), nil
```

**File:** kaiax/auction/impl/execution.go (L96-100)
```go
	// 3. Read gas buffer estimate
	bidTxGasBuffer, err = system.ReadGasBufferEstimate(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}
```

**File:** blockchain/vm/gas_table.go (L345-368)
```go
func gasCallCode(evm *EVM, contract *Contract, stack *Stack, mem *Memory, memorySize uint64) (uint64, error) {
	memoryGas, err := memoryGasCost(mem, memorySize)
	if err != nil {
		return 0, err
	}
	var (
		gas      uint64
		overflow bool
	)
	if stack.Back(2).Sign() != 0 {
		gas += params.CallValueTransferGas
	}
	if gas, overflow = math.SafeAdd(gas, memoryGas); overflow {
		return 0, errGasUintOverflow
	}
	evm.callGasTemp, err = callGas(contract.Gas, gas, stack.Back(0))
	if err != nil {
		return 0, err
	}
	if gas, overflow = math.SafeAdd(gas, evm.callGasTemp); overflow {
		return 0, errGasUintOverflow
	}
	return gas, nil
}
```

**File:** blockchain/gaspool.go (L34-41)
```go
// AddGas makes gas available for execution.
func (gp *GasPool) AddGas(amount uint64) *GasPool {
	if uint64(*gp) > math.MaxUint64-amount {
		panic("gas pool pushed above uint64")
	}
	*(*uint64)(gp) += amount
	return gp
}
```

**File:** blockchain/types/tx_internal_data.go (L556-592)
```go
// Klaytn-TxTypes since genesis, and EthTxTypes since istanbul use this.
func IntrinsicGasPayload(gas uint64, data []byte, isContractCreation bool, rules params.Rules) (uint64, error) {
	// Bump the required gas by the amount of transactional data
	length := uint64(len(data))
	if length > 0 {
		// Zero and non-zero bytes are priced differently
		z := uint64(bytes.Count(data, []byte{0}))
		nz := length - z

		// Since the genesis block, a flat 100 gas is paid
		// regardless of whether the value is zero or non-zero.
		nonZeroGas, zeroGas := params.TxDataGas, params.TxDataGas
		if rules.IsPrague {
			nonZeroGas = params.TxDataNonZeroGasEIP2028
			zeroGas = params.TxDataZeroGas
		}
		// Make sure we don't exceed uint64 for all data combinations
		if (math.MaxUint64-gas)/nonZeroGas < nz {
			return 0, ErrGasUintOverflow
		}
		gas += nz * nonZeroGas

		if (math.MaxUint64-gas)/zeroGas < z {
			return 0, ErrGasUintOverflow
		}
		gas += z * zeroGas
	}

	if isContractCreation && rules.IsShanghai {
		lenWords := toWordSize(length)
		if (math.MaxUint64-gas)/params.InitCodeWordGas < lenWords {
			return 0, ErrGasUintOverflow
		}
		gas += lenWords * params.InitCodeWordGas
	}
	return gas, nil
}
```

**File:** kaiax/auction/impl/getter.go (L27-47)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
		}

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}
```
