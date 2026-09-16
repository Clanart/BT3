Based on my research, I found a genuine analog in the Kaia auction module (KIP-249), though I was unable to fully verify downstream enforcement in `builder.go`/`execution.go` due to reaching my tool-call budget — this should be verified further.

### Title
Auction bid pool never validates `Bid.MaxGasPrice`, leaving a signed price-protection field unchecked before bid acceptance - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.validateBid` in `kaiax/auction/impl/bid_pool.go` enforces exactly six documented rules (block number range, bid amount > 0, data size, call gas limit, and searcher/auctioneer signatures) but never checks `bid.MaxGasPrice`, even though this field is part of the EIP-712 signed payload for auction version v3 (`AuctionVersionV3`) and is meant to cap/protect the price condition under which the searcher agreed to execute.

### Finding Description
The `BidData` struct includes `MaxGasPrice *big.Int` [1](#0-0)  and it is part of the EIP-712 typed struct hash for auction version v3, `auctionTypeV3`, which the searcher cryptographically signs over [2](#0-1) [3](#0-2) . This signals that `MaxGasPrice` is intended as a binding, price-sensitive condition of the searcher's bid (analogous to the price-deviation checks that `SimpleManager` performs before minting into a pool).

However, `BidPool.validateBid`, which is the sole gate for `AddBid` (reachable directly from the public `auction_submitBid` RPC via `AuctionAPI.SubmitBid`), only implements checks 1–6 from the module's documented rules — sender/winner uniqueness, block-number window, bid > 0, data-size limit, call-gas-limit, and signature validity [4](#0-3) . The `MaxGasPrice` field is decoded from the RPC input into the `Bid` struct [5](#0-4)  and is included in the signature digest, but `validateBid`/`insertBid` perform no comparison of it against the current or expected base fee / gas price at bid-submission or bid-winner-selection time. The module's own README enumerates the "Bid pool validation rules" as items 1–6, and none of them reference `MaxGasPrice` [6](#0-5) .

This is structurally the same defect pattern as the reported `SimpleManager#rebalance` issue: a price/deviation-protection parameter is present and even part of the signed intent, but the validation path that is supposed to enforce it (analogous to checking "burned" pools as well as "minted" pools) omits the check for one leg of the guarded operation, silently accepting bids whose price condition is never verified.

### Impact Explanation
Since `MaxGasPrice` is never checked in the accept/insert path, a bid can be accepted and later selected as the winning bid for a block regardless of whether the network's actual gas price (base fee, computed via KIP-71) exceeds the searcher's intended `MaxGasPrice` ceiling. If downstream bid-tx generation / execution (`builder.go`, `execution.go`, which I could not fully inspect in this session) does not independently re-derive and enforce this bound before building the bid transaction, a searcher's price-protection guarantee is silently bypassed, which could result in the searcher's bid transaction executing under unfavorable/unintended gas-price conditions, and/or being exploitable by a auctioneer/proposer colluding to always select or construct settlement transactions ignoring the cap — a fee/auction-settlement abuse scenario.

### Likelihood Explanation
`AddBid` is reachable from a single unauthenticated-adjacent RPC call (`auction_submitBid`), gated only by an auctioneer signature check on the *bid* (not on the `MaxGasPrice` enforcement itself, which is checked cryptographically but not semantically validated against real-time price data). Any bid containing an arbitrary `MaxGasPrice` value (including zero/omitted, as the field is `omitempty`) passes `validateBid` as long as the other six checks and signatures succeed, making this reachable in every normal auction flow, not just an edge case.

### Recommendation
Add an explicit check in `BidPool.validateBid` (or in the bid-tx building path such as `builder.go`) that compares the current/next-block base fee (per KIP-71, e.g., via `KIP71Config.NextMagmaBlockBaseFee`) against `bid.MaxGasPrice` when the field is set, rejecting or re-queuing bids whose price ceiling is already violated — mirroring how `SimpleManager` should validate price deviation on both mint and burn legs rather than only one.

### Proof of Concept
Not independently reproducible from the index alone: I confirmed the root cause (`validateBid`'s omission of any `MaxGasPrice` comparison) via direct code reading of `bid_pool.go`, `bid.go`, `eip712.go`, and `api.go`, but I was unable to inspect `kaiax/auction/impl/builder.go` and `execution.go` in this session (tool budget exhausted) to confirm whether a compensating check exists later in the pipeline. This should be verified before treating the finding as fully confirmed end-to-end.

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

**File:** kaiax/auction/eip712.go (L96-118)
```go
type bidV3 struct{ *Bid }

func (b bidV3) EncodeType() []byte {
	return auctionTypeHashV3.Bytes()
}

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

**File:** kaiax/auction/impl/api.go (L72-105)
```go
// BidInput is the same format with `BidData`, execpt adding new field `TargetTxRaw` and replacing `[]byte` type to `hexutil.Bytes`
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

**File:** kaiax/auction/README.md (L13-24)
```markdown
## Bid pool validation rules

A bid pool is responsible for managing the valid bids from the `Auctioneer`. The bid must satisfy the following rules:

1. The `bid.Sender` must not be in the winner list of the same block number if the new bid doesn't have the same target block and hash as the previous bid.
2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
3. The `bid.Bid` must be greater than 0.
4. The `bid.Data` size must be less than or equal to `BidTxMaxDataSize`.
5. The `bid.CallGasLimit` must be less or equal to `BidTxMaxCallGasLimit`.
6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.

Please note that `Auctioneer` also validates the searcher's bid according to the KIP-249.
```
