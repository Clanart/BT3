### Title
Searcher cannot bound the gas price paid by their auction bid, allowing bid execution at unbounded cost (v2.1 `AuctionEntryPoint`) - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
The `MaxGasPrice` field exists on `auction.BidData` and is only honored in the "0.0.2" (`AuctionVersionV3`) EIP-712 typehash and ABI encoding. For the "0.0.1" (`AuctionVersionV2`) auction entry point, which the client still fully supports, a searcher's signed bid has no field constraining the maximum gas price the bid transaction may be executed/settled at — directly analogous to the reported bug class where a committing party (there, a borrower; here, a searcher) cannot bound a variable cost parameter (`rate`) determined outside their control at settlement time.

### Finding Description
`auction.BidData` carries an optional `MaxGasPrice *big.Int` used to bound the gas price a bid may be executed at, but this bound is wired in only for `AuctionVersionV3`: [1](#0-0) [2](#0-1) [3](#0-2) 

For `AuctionVersionV2`, the EIP-712 struct hash (`auctionType`, no `maxGasPrice` field) and ABI encoding (`IAuctionEntryPointAuctionTx` without `MaxGasPrice`) mean a V2 searcher's signature never commits to any cap on gas price: [4](#0-3) [5](#0-4) 

`BidPool.validateBid` (the pool admission rule set) validates block-number range, non-zero bid, data size, call-gas-limit, and signatures — but never checks or enforces any gas-price bound for the bid, even when `MaxGasPrice` is populated by the caller: [6](#0-5) 

Furthermore, the bid transaction itself is built using the *target transaction's* fee cap/tip cap, not any searcher-specified bound: [7](#0-6) 

This mirrors the Astaria pattern precisely: a party (searcher/borrower) commits (signs a bid / commits to a lien) to an action whose settlement-time cost parameter (gas price / rate) is determined by external, mutable state (network base fee / strategy validator) at execution time, with the committing party's own protocol version (V2.1) providing no mechanism to cap that parameter, while an upper-bound protection was later recognized as necessary and added for a newer variant (V3.0/`MaxGasPrice`) — confirming this is a real, protocol-recognized gap in the older path that remains reachable as long as an entry point still reports `AUCTION_VERSION() == "0.0.1"`.

### Impact Explanation
If the operator/CN reads `AUCTION_VERSION` "0.0.1" from an active `AuctionEntryPoint` (a legitimate, reachable configuration path via `ReadAuctionVersion`/`updateAuctionInfo`), every accepted bid for that block is unconditionally exposed to the ambient base fee/tip at inclusion time with no searcher-specified ceiling. A searcher who signed a bid expecting normal-range gas pricing can have their bid transaction executed at an arbitrarily higher gas price if the base fee spikes between bid signing and block inclusion (e.g., due to a KIP-71/Magma base-fee jump within the `allowFutureBlock` window), causing the searcher to pay materially more than intended for the same `Bid`/`CallGasLimit` — an uncapped-cost/value-loss condition for a class of unprivileged, permissionless participants (auction bidders), with no corresponding recourse since the bid transaction is generated and submitted automatically by the block-builder using the target tx's fee cap.

### Likelihood Explanation
This requires only a normal, permissionless auction bid submission (`auction_submitBid`, no privileged access) against an active entry point that is (or reverts to) `AUCTION_VERSION` "0.0.1"; `updateAuctionInfo` runs every block and simply reads whatever entry point/version the on-chain registry currently reports, so any deployment or rollback to a V2.1 entry point re-exposes this gap network-wide without any code change to the Kaia client itself. Given that `allowFutureBlock` permits bids to target several blocks ahead, ordinary base-fee volatility during that window is sufficient to trigger the unbounded-cost scenario, making the likelihood moderate-to-high wherever a V2.1 entry point is in use.

### Recommendation
- Require and enforce `MaxGasPrice` for all supported auction versions, not just `AuctionVersionV3`; either deprecate/reject V2.1 entry points at the client level, or extend `validateBid` in `BidPool` to require and check a gas-price bound (analogous to the `rate` upper-bound recommendation) before admitting a bid.
- In `GetBidTxGenerator`, when a `MaxGasPrice` (or equivalent field) is present, cap the constructed bid transaction's `GasFeeCap`/`GasTipCap` to that bound rather than always inheriting the target transaction's fee cap.
- Surface a clear validation error (comparable to `ErrExceedMaxCallGasLimit`) when a bid's realized/derived gas price would exceed its declared bound, mirroring how `duration`/`rate` bounds are recommended to be enforced against fetched lien details in the original report.

### Proof of Concept
1. Deploy/activate an `AuctionEntryPoint` reporting `AUCTION_VERSION() == "0.0.1"` (`AuctionVersionV2`) via the system registry, so `updateAuctionInfo` in `kaiax/auction/impl/execution.go` picks it up (`ReadAuctionVersion` returns `"0.0.1"`).
2. As an unprivileged searcher, sign and submit a bid via `auction_submitBid` with a normal `Bid`/`CallGasLimit`, targeting `blockNumber = current+N` (`N <= allowFutureBlock`). Because `AuctionVersionV2`'s EIP-712 type (`auctionType` in `kaiax/auction/eip712.go`) excludes `maxGasPrice`, the signed digest carries no gas-price bound.
3. `BidPool.validateBid` (`kaiax/auction/impl/bid_pool.go:345-395`) admits the bid purely on block-number range, non-zero bid, data-size, call-gas-limit, and signature checks — no gas-price check exists.
4. Before block `current+N` is produced, the network base fee spikes (e.g., due to a burst of high-fee transactions under KIP-71/Magma dynamics).
5. `GetBidTxGenerator` (`kaiax/auction/impl/getter.go:27-67`) builds and signs the bid transaction using the *target transaction's* `GasFeeCap()`/`GasTipCap()` — which, being unrelated to the searcher's original cost expectations, may itself be elevated — and the bid is executed at that price with no cap ever having been agreed to by the searcher, demonstrating the missing slippage/price-bound protection for the V2.1 auction path.

### Citations

**File:** kaiax/auction/bid.go (L32-44)
```go
type BidData struct {
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           *big.Int       `json:"bid"`
	MaxGasPrice   *big.Int       `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          []byte         `json:"data"`
	SearcherSig   []byte         `json:"searcherSig"`
	AuctioneerSig []byte         `json:"auctioneerSig"`
}
```

**File:** blockchain/system/auction.go (L83-119)
```go
func EncodeAuctionCallData(bid *auction.Bid, version string) ([]byte, error) {
	if version == auction.AuctionVersionV3 {
		maxGasPrice := bid.MaxGasPrice
		if maxGasPrice == nil {
			maxGasPrice = new(big.Int)
		}
		input := contractsv3.IAuctionEntryPointAuctionTx{
			TargetTxHash:  bid.TargetTxHash,
			BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
			Sender:        bid.Sender,
			To:            bid.To,
			Nonce:         new(big.Int).SetUint64(bid.Nonce),
			Bid:           bid.Bid,
			MaxGasPrice:   maxGasPrice,
			CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
			Data:          bid.Data,
			SearcherSig:   bid.SearcherSig,
			AuctioneerSig: bid.AuctioneerSig,
		}
		return abiV3.Pack("call", input)
	}

	// auction.AuctionVersionV2 or unknown version: default to v2.1 ABI.
	input := contracts.IAuctionEntryPointAuctionTx{
		TargetTxHash:  bid.TargetTxHash,
		BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
		Sender:        bid.Sender,
		To:            bid.To,
		Nonce:         new(big.Int).SetUint64(bid.Nonce),
		Bid:           bid.Bid,
		CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
		Data:          bid.Data,
		SearcherSig:   bid.SearcherSig,
		AuctioneerSig: bid.AuctioneerSig,
	}
	return abi.Pack("call", input)
}
```

**File:** kaiax/auction/eip712.go (L26-35)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
	EIP712DomainType = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
	auctionName      = "KAIA_AUCTION"
	// Auction version strings match the on-chain AUCTION_VERSION view of the
	// corresponding entry-point contract. v3.0 selects the typehash that
	// includes maxGasPrice.
	AuctionVersionV2 = "0.0.1"
	AuctionVersionV3 = "0.0.2"
```

**File:** kaiax/auction/eip712.go (L69-86)
```go
}

func (b *Bid) EncodeType() []byte {
	return auctionTypeHash.Bytes()
}

func (b *Bid) EncodeData() []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
}
```

**File:** contracts/bindings/auction/Kip249.go (L32-44)
```go
// IAuctionEntryPointAuctionTx is an auto generated low-level Go binding around an user-defined struct.
type IAuctionEntryPointAuctionTx struct {
	TargetTxHash  [32]byte
	BlockNumber   *big.Int
	Sender        common.Address
	To            common.Address
	Nonce         *big.Int
	Bid           *big.Int
	CallGasLimit  *big.Int
	Data          []byte
	SearcherSig   []byte
	AuctioneerSig []byte
}
```

**File:** kaiax/auction/impl/bid_pool.go (L345-395)
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

	curBlock := bp.Chain.CurrentBlock()
	if curBlock == nil {
		return auction.ErrBlockNotFound
	}

	// 2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
	curNum := curBlock.NumberU64()
	if blockNumber <= curNum || blockNumber > curNum+allowFutureBlock {
		return auction.ErrInvalidBlockNumber
	}

	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}

	// 4. The data size must be less than the maximum limit.
	if uint64(len(bid.Data)) > BidTxMaxDataSize {
		return auction.ErrExceedMaxDataSize
	}

	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}

	// 6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.
	if err := bp.validateBidSigs(bid); err != nil {
		return err
	}

	return nil
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
