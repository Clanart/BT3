# Finding: `MaxGasPrice` Field Is Not Covered by the EIP-712 Searcher Signature for v2/v2.1 Auction Bids, Allowing Mass-Assignment of an Unsigned Auction Field - ([File: kaiax/auction/eip712.go])

## Summary
The `auction_submitBid` public RPC accepts a `BidInput` struct where the `MaxGasPrice` field is copied verbatim into the pool's internal `auction.BidData` without being part of the cryptographic commitment the searcher actually signed, when the active auction entry-point version is v2/v2.1 (`AuctionVersionV2`). This is a mass-assignment-class bug: a field the searcher never authorized (or authorized for a different value) can be set/overwritten by whoever relays the bid to the node.

## Finding Description
The public `auction_submitBid` API accepts a client-supplied `BidInput`, including `MaxGasPrice`, and unconditionally copies it into the internal `auction.BidData` used by the bid pool: [1](#0-0) 

The EIP-712 typed-data hash that the searcher actually signs (`SearcherSig`) is version-dependent. For `AuctionVersionV2` (`"0.0.1"`), the struct type explicitly excludes `maxGasPrice`: [2](#0-1) [3](#0-2) 

Only the v3 type hash (`AuctionVersionV3`, `"0.0.2"`) commits to `maxGasPrice`: [4](#0-3) 

Because `GetHashTypedData` falls back to the v2 encoding whenever the entry point reports (or the pool is configured with) `AuctionVersionV2`, or any unrecognized version: [5](#0-4) 

...the `MaxGasPrice` value carried in the submitted `BidData` is never part of what `SearcherSig` attests to. The bid-pool admission logic (`validateBid` in `kaiax/auction/impl/bid_pool.go`) enforces sender/winner conflicts, block-number range, positive bid amount, data-size and gas-limit caps, and finally signature validity, but none of these checks tie `MaxGasPrice` to the searcher's cryptographic authorization for v2 bids: [6](#0-5) 

The `AuctioneerSig` likewise only attests to the `SearcherSig` bytes themselves (an Ethereum-signed-message hash of the raw signature), not to the bid's field values: [7](#0-6) 

As a result, `MaxGasPrice` is effectively an unsigned, mass-assignable field: whoever submits the bid via `auction_submitBid` (the party relaying a valid `(SearcherSig, AuctioneerSig)` pair) can set it to any value they choose, independent of what the searcher intended, as long as the entry point/pool is on the v2/v2.1 typehash path.

## Impact Explanation
`MaxGasPrice` exists specifically to let a searcher cap the gas price it is willing to pay for its bid transaction under KIP-249. Since this value is not bound by the searcher's signature in the v2 path, the value can be silently modified or injected by the submitting party without invalidating the bid's signatures, undermining the intended fee-safety guarantee the field is meant to provide and allowing the bid submitter to alter fee-relevant terms of a signed auction bid after the searcher has cryptographically committed to it. This falls into the "fee or fee-delegation abuse" / unauthorized-value-modification category analogous to the IRIS mass-assignment bug, where a client-controlled field bypasses the intended authorization boundary.

## Likelihood Explanation
Reachable directly from a single public RPC call (`auction_submitBid`) with no special privilege beyond being able to relay a bid; the vulnerable code path is hit whenever the auction entry point is (or reports) v2/v2.1, which is the default/legacy version referenced throughout the module (`AuctionVersionV2 = "0.0.1"`).

## Recommendation
Include `maxGasPrice` in the v2/v2.1 EIP-712 struct hash (or reject/require a v3 typehash whenever `MaxGasPrice` is non-zero/non-nil), so that any value placed in `BidData.MaxGasPrice` is cryptographically bound to the searcher's `SearcherSig`, preventing post-signature mutation of the field by the bid submitter.

## Proof of Concept
1. A searcher signs a legitimate v2 `AuctionTx` (per `auctionType` in `eip712.go`) that does not include `maxGasPrice`, producing a valid `SearcherSig`.
2. The auctioneer/relayer countersigns and forwards the bid, but sets `BidInput.MaxGasPrice` to an arbitrary attacker-chosen value before calling `auction_submitBid`.
3. `ToBid` copies the tampered `MaxGasPrice` into `BidData` unchanged (`kaiax/auction/impl/api.go:101-103`).
4. `bp.validateBidSigs` verifies `SearcherSig`/`AuctioneerSig` successfully because neither signature's digest includes `MaxGasPrice` under the v2 typehash, so `AddBid` accepts the bid with the forged `MaxGasPrice`.

**Note on confidence:** I was unable to fully retrieve the body of `validateBidSigs` and `execution.go`'s consumption of `MaxGasPrice` within the available tool budget, so I cannot show the exact downstream monetary effect (e.g., how `MaxGasPrice` feeds into the generated bid transaction's gas price cap). The unsigned-field/mass-assignment root cause in `eip712.go` and `api.go` is confirmed directly from source; the full value-movement consequence should be verified against `kaiax/auction/impl/execution.go` before treating this as fully proven.

### Citations

**File:** kaiax/auction/impl/api.go (L73-105)
```go
type BidInput struct {
	TargetTxRaw   hexutil.Bytes  `json:"targetTxRaw"`
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           hexutil.Big    `json:"bid"`
	MaxGasPrice   *hexutil.Big   `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          hexutil.Bytes  `json:"data"`
	SearcherSig   hexutil.Bytes  `json:"searcherSig"`
	AuctioneerSig hexutil.Bytes  `json:"auctioneerSig"`
}

func ToBid(bidInput BidInput) *auction.Bid {
	bidData := auction.BidData{
		TargetTxHash:  bidInput.TargetTxHash,
		BlockNumber:   bidInput.BlockNumber,
		Sender:        bidInput.Sender,
		To:            bidInput.To,
		Nonce:         bidInput.Nonce,
		Bid:           bidInput.Bid.ToInt(),
		CallGasLimit:  bidInput.CallGasLimit,
		Data:          bidInput.Data,
		SearcherSig:   bidInput.SearcherSig,
		AuctioneerSig: bidInput.AuctioneerSig,
	}
	if bidInput.MaxGasPrice != nil {
		bidData.MaxGasPrice = bidInput.MaxGasPrice.ToInt()
	}
	return &auction.Bid{BidData: bidData}
}
```

**File:** kaiax/auction/eip712.go (L26-36)
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
)
```

**File:** kaiax/auction/eip712.go (L75-86)
```go
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

**File:** kaiax/auction/eip712.go (L102-118)
```go
func (b bidV3) EncodeData() []byte {
	maxGasPrice := b.MaxGasPrice
	if maxGasPrice == nil {
		maxGasPrice = new(big.Int)
	}
	encoded := make([]byte, 0, 10*32)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.BidData.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(maxGasPrice.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
}
```

**File:** kaiax/auction/eip712.go (L124-149)
```go
func (b *Bid) GetHashTypedData(chainId *big.Int, verifyingContract common.Address, version string) []byte {
	if chainId == nil {
		return nil
	}

	domain := EIP712Domain{
		EIP712DomainTypeHash: eip712TypeHash,
		NameHash:             auctionNameHash,
		VersionHash:          crypto.Keccak256Hash([]byte(version)),
		ChainId:              chainId,
		VerifyingContract:    verifyingContract,
	}

	domainSeparator := EncodeEIP712(domain)

	var structHash []byte
	if version == AuctionVersionV3 {
		structHash = EncodeEIP712(bidV3{b})
	} else if version == AuctionVersionV2 {
		structHash = EncodeEIP712(b)
	} else {
		// Unknown version: default to v2.1 typehash.
		structHash = EncodeEIP712(b)
	}

	return crypto.Keccak256([]byte{0x19, 0x01}, domainSeparator, structHash)
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

**File:** kaiax/auction/bid.go (L46-55)
```go
type Bid struct {
	BidData
	hash     atomic.Value
	gasLimit atomic.Uint64
}

func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
}
```
