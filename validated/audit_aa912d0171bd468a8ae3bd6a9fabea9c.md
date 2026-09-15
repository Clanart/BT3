### Title
Hardcoded `BidTxMaxCallGasLimit` bid-pool cap can desynchronize from the admin-mutable `AuctionEntryPoint.gasBufferEstimate`, causing auction bid rejection/DoS - (File: kaiax/auction/impl/bid_pool.go)

### Summary
The auction (MEV) module's bid pool enforces a client-hardcoded ceiling `BidTxMaxCallGasLimit = uint64(10_000_000)` on every submitted bid's `CallGasLimit`, while the actual gas overhead the `AuctionEntryPoint` system contract needs to wrap and execute that call (`gasBufferEstimate`) is read dynamically from the contract and can be changed by the contract admin, mirroring the Chainlink VRF pattern where a client hardcodes parameters that are actually validated/controlled by an external, independently-governed contract.

### Finding Description
`BidPool.validateBid` rejects any bid whose `CallGasLimit` exceeds the hardcoded constant `BidTxMaxCallGasLimit`: [1](#0-0) , with the constant itself defined at [2](#0-1) .

Separately, the module reads a mutable, admin-settable value `gasBufferEstimate` from the live `AuctionEntryPoint` contract every block via `system.ReadGasBufferEstimate` and stores it as `bidTxGasBuffer`, used to compute the final `BidTx.GasLimit` sent on-chain: [3](#0-2) . The test/mock contract demonstrates this value is admin-configurable (`setGasBufferEstimate`) and defaults to `180_000`, but nothing in that admin's contract enforces any relationship with the client's hardcoded `BidTxMaxCallGasLimit`: [4](#0-3) .

Because `bidTxGasBuffer` is fetched from an externally upgradeable/admin-controlled system contract (via `SystemRegistry`) while `BidTxMaxCallGasLimit` remains a compile-time Go constant in the client, the two values can drift out of sync exactly as in the VRF analog: the contract admin can raise `gasBufferEstimate` (e.g., due to a more expensive `AuctionEntryPoint` implementation) such that legitimate high-gas searcher bids that pass the client-side `BidTxMaxCallGasLimit` check nonetheless produce a `BidTx` whose total gas (`CallGasLimit + bidTxGasBuffer`) exceeds the block gas limit or otherwise fails on-chain, or conversely the hardcoded cap becomes stale and either over-restrictive (rejecting valid, safe bids) or under-restrictive relative to the contract's real needs.

### Impact Explanation
If the on-chain gas requirement diverges from the hardcoded client assumption, the auction module can systematically reject valid searcher bids or generate `BidTx`s that revert/fail during block building, stalling MEV auction settlement network-wide since every CN independently enforces the same stale constant `BidTxMaxCallGasLimit`. Because the module clears the bid pool whenever `bidTxGasBuffer`/entry point address changes (`updateAuctionInfo` triggers `clearBidPool`), the client relies on assumptions baked into `BidTxMaxCallGasLimit` remaining valid across contract upgrades it does not control, which is a direct analog to the VRF issue: the client has no ability to reconcile its hardcoded validation ceiling with the live, admin-mutable parameter, and there is no governance-tunable knob (only a compiled constant) to adjust `BidTxMaxCallGasLimit` if the entry point contract's gas needs change. This is bounded to service disruption/DoS of the auction module rather than fund loss, limiting it to Medium severity.

### Likelihood Explanation
Likelihood is moderate: it requires the `AuctionEntryPoint` system contract admin to change `gasBufferEstimate` (or deploy an upgraded entry point with materially different gas overhead) to a value incompatible with the hardcoded `BidTxMaxCallGasLimit`, which is plausible given the module already supports live contract upgrades (v2.1 → v3.0 detection via `AUCTION_VERSION`) as seen in `system.ReadAuctionVersion`: [5](#0-4) , indicating the entry point is expected to evolve.

### Recommendation
Read the maximum allowed `CallGasLimit` from the `AuctionEntryPoint` contract (or via a governance parameter) instead of hardcoding `BidTxMaxCallGasLimit` in `kaiax/auction/impl/bid_pool.go`, so the bid-pool validation ceiling can track contract-side changes to gas requirements without requiring a client hard fork.

### Proof of Concept
1. Contract admin increases `AuctionEntryPoint.gasBufferEstimate` substantially (or deploys a new entry point implementation with higher intrinsic gas overhead), reflected on-chain and read by `updateAuctionInfo`/`system.ReadGasBufferEstimate`. [6](#0-5) 
2. A searcher submits a bid with `CallGasLimit` close to the still-hardcoded `BidTxMaxCallGasLimit` (10,000,000). [1](#0-0) 
3. `getBidTxGasLimit`/`GetBidTxGenerator` computes the final `BidTx.GasLimit` combining `CallGasLimit` and the now-larger `bidTxGasBuffer`, producing a transaction that either exceeds block gas limit or fails on execution, causing the winning bid's post-transaction settlement to fail even though it passed client-side validation. [7](#0-6)

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L38-48)
```go
const (
	bidChSize        = 2048
	allowFutureBlock = 2

	BidTxMaxCallGasLimit = uint64(10_000_000)
	BidTxMaxDataSize     = uint64(64 * 1024) // 64KB

	// Rate limiting
	bidsPerSecondPerPeer = 300 // Max bids per second per peer
	rateLimiterCacheSize = 1024
)
```

**File:** kaiax/auction/impl/bid_pool.go (L384-387)
```go
	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}
```

**File:** kaiax/auction/impl/execution.go (L52-100)
```go
// updateAuctionInfo updates the auctioneer address and auction entry point address for the given block number.
// It expects the `num` is after Randao fork.
// It returns true if the non-zero auctioneer address and auction entry point address are set, otherwise false.
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
```

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L37-52)
```text
    address public auctioneer;
    uint256 public gasBufferEstimate = 180_000;
    uint256 public count;

    modifier onlyProposer() {
        if (msg.sender != block.coinbase) revert();
        _;
    }

    function setAuctioneer(address _auctioneer) public {
        auctioneer = _auctioneer;
    }

    function setGasBufferEstimate(uint256 _gasBufferEstimate) public {
        gasBufferEstimate = _gasBufferEstimate;
    }
```

**File:** blockchain/system/auction.go (L64-77)
```go
// ReadAuctionVersion reads the AUCTION_VERSION view from the given entry-point
// contract. The result distinguishes v2.1 ("0.0.1") from v3.0 ("0.0.2") so
// callers can pick the appropriate EIP-712 typehash and ABI.
//
// The v3.0 binding is used for the call: AUCTION_VERSION() is a public-constant
// getter with the same selector on both v2.1 and v3.0 contracts, so the same
// typed binding works against either deployment.
func ReadAuctionVersion(backend bind.ContractCaller, contractAddr common.Address, num *big.Int) (string, error) {
	caller, err := contractsv3.NewIAuctionEntryPointCaller(contractAddr, backend)
	if err != nil {
		return "", err
	}
	return caller.AUCTIONVERSION(&bind.CallOpts{BlockNumber: num})
}
```

**File:** kaiax/auction/impl/getter.go (L27-67)
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

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &auctionEntryPoint,
			types.TxValueKeyAmount:     common.Big0,
			types.TxValueKeyData:       data,
			types.TxValueKeyGasLimit:   bid.GetGasLimit(),
			types.TxValueKeyGasFeeCap:  tx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  tx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)

		return tx, err
	}
```
