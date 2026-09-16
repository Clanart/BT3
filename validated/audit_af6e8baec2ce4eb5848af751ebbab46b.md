### Title
EIP-712 bid signature omits `MaxGasPrice` for auction protocol version V2, allowing a mutable field to be forged without invalidating the searcher's signature - (File: kaiax/auction/eip712.go)

### Summary
The Kaia auction (MEV) module signs and hashes `Bid` data inconsistently: the pool-level anti-duplicate hash (`Bid.Hash()`) commits to the *entire* `BidData` struct (including `MaxGasPrice`), while the EIP-712 digest that the searcher actually signs (`ValidateSearcherSig`) omits `MaxGasPrice` whenever the auction entry point reports any version other than `"0.0.2"` (`AuctionVersionV3`). This is structurally the same class of bug as the ZetaChain finding: a field that is not covered by the value that is supposed to authenticate/deduplicate the message (there: the CCTX ballot index; here: the searcher's signature) can be freely mutated by a third party while the authorization check still passes, letting an attacker present a bid with forged economic terms as if it were validly authorized by the searcher.

### Finding Description
`kaiax/auction/eip712.go` defines two EIP-712 struct type-hashes: [1](#0-0) 

`GetHashTypedData` selects the struct hash based on the on-chain `AUCTION_VERSION` string: [2](#0-1) 

Only when `version == AuctionVersionV3` ("0.0.2") is `bidV3.EncodeData()` used, which includes `MaxGasPrice` in the signed struct: [3](#0-2) 

For `AuctionVersionV2` **and for any unrecognized version string**, the code falls back to `Bid.EncodeData()`, which does **not** include `MaxGasPrice` at all: [4](#0-3) 

`ValidateSearcherSig` verifies the searcher's signature against exactly this digest: [5](#0-4) 

Meanwhile, `BidPool` uses `Bid.Hash()` — which commits to the *full* `BidData` including `MaxGasPrice` via RLP — as the sole anti-replay/anti-duplicate key in the pool: [6](#0-5) [7](#0-6) [8](#0-7) 

Because `MaxGasPrice` is excluded from the V2/legacy signature digest but included in `Bid.Hash()`, anyone who observes a validly-signed V2 bid (broadcast via `HandleBid`, gossiped between peers, or returned by an RPC/API) can take that same `SearcherSig`/`AuctioneerSig` pair and re-wrap it with a **different `MaxGasPrice`** value. `ValidateSearcherSig` will still pass (since the digest it recomputes does not depend on `MaxGasPrice` for this version), yet `bid.Hash()` differs from the original, so the pool's `ErrBidAlreadyExists` de-duplication in `validateBid`/`insertBid` does **not** reject it as a replay: [9](#0-8) 

`senderHasDifferentWinner`, the other anti-forgery gate, only compares `BlockNumber` and `TargetTxHash` (not `MaxGasPrice` or bid amount) when deciding whether a bid is "the same" as the current winner: [10](#0-9) 

so a forged bid with tampered `MaxGasPrice` for the same target/sender is treated as an update path rather than an impersonation attempt, and can overwrite the legitimately signed bid in `bp.bidMap` / `bp.bidTargetMap`.

### Impact Explanation
`MaxGasPrice` is intended to be an economic cap the searcher committed to via signature (this is exactly why `bidV3` was introduced to add it to the signed struct). By falling back to the V2 typehash for any auction entry point that does not explicitly report version `"0.0.2"`, the module allows a bid's `MaxGasPrice` to be altered post-signature without invalidating `ValidateSearcherSig`. Any unprivileged entity that can observe a broadcast bid (a normal auction participant, not a validator/operator) can resubmit it with an attacker-chosen `MaxGasPrice`, and this forged bid will be accepted into `BidPool` and can become the block's winning bid because pool admission never re-derives `MaxGasPrice` from anything the searcher actually authorized. This constitutes fee/auction-settlement abuse (unauthorized manipulation of the searcher's fee/priority cap) reachable purely from a public bid submission path, matching the "gasless/auction settlement theft" and "fee abuse" categories in scope.

### Likelihood Explanation
Exploitation requires only observing one legitimately signed bid targeting an entry point running the (default/fallback) V2 protocol version and re-emitting it with a modified `MaxGasPrice`; no validator, node-operator, or cryptographic key compromise is needed. The condition is also triggered by any unrecognized/misreported `AUCTION_VERSION` string (defensive fallback), broadening exposure beyond intentionally-configured V2 auctioneers.

### Recommendation
Make the anti-duplicate/anti-forgery key and the signed digest consistent for every supported (and default/fallback) protocol version:
- Either always include `MaxGasPrice` (and any other economically-relevant field) in the EIP-712 struct hash regardless of version, or
- Reject bids whose declared version is not explicitly recognized (remove the silent "any other value falls back to v2.1" branch in `GetHashTypedData`), and
- Have `senderHasDifferentWinner`/pool replacement logic compare the full economically-relevant bid content (not just `BlockNumber`/`TargetTxHash`) before treating an incoming bid as an "update" of an existing winner.

### Proof of Concept
1. Configure/observe an auction entry point whose `ReadAuctionVersion` returns anything other than `"0.0.2"` (e.g., `"0.0.1"` or an unrecognized string) — `updateAuctionInfo` in `kaiax/auction/impl/execution.go` will propagate this version to `BidPool.validateBidSigs`.
2. Capture a valid `auction.Bid` broadcast by a legitimate searcher (via `HandleBid`), containing `SearcherSig` computed over `Bid.EncodeData()` (no `MaxGasPrice`).
3. Construct a new `Bid` with identical `BidData` fields except `MaxGasPrice` set to an attacker-chosen value, keeping the same `SearcherSig`/`AuctioneerSig`.
4. Call `BidPool.AddBid` (or the equivalent RPC/API) with the tampered bid: `validateBidSigs` → `ValidateSearcherSig` recomputes the V2 digest (still excludes `MaxGasPrice`) and succeeds; `bid.Hash()` differs from the original (since `MaxGasPrice` is part of RLP-hashed `BidData`), so `ErrBidAlreadyExists` does not fire; `senderHasDifferentWinner` returns false because `Equals` only checks `BlockNumber`/`TargetTxHash`.
5. The tampered bid is accepted into `bp.bidMap`/`bp.bidTargetMap`/`bp.bidWinnerMap`, potentially replacing or coexisting with the original as the winning bid with a `MaxGasPrice` the searcher never actually authorized.

### Citations

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

**File:** kaiax/auction/eip712.go (L139-149)
```go
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

**File:** kaiax/auction/bid.go (L70-77)
```go
func (b *Bid) Hash() common.Hash {
	if hash := b.hash.Load(); hash != nil {
		return hash.(common.Hash)
	}
	hash := rlpHash(b.BidData)
	b.hash.Store(hash)
	return hash
}
```

**File:** kaiax/auction/bid.go (L94-115)
```go
func (b *Bid) ValidateSearcherSig(chainId *big.Int, verifyingContract common.Address, version string) error {
	if chainId == nil {
		return ErrNilChainId
	}

	if common.EmptyAddress(verifyingContract) {
		return ErrNilVerifyingContract
	}

	digest := b.GetHashTypedData(chainId, verifyingContract, version)

	recoveredSender, err := getSigner(b.SearcherSig, digest)
	if err != nil {
		return fmt.Errorf("failed to recover searcher sig: %v", err)
	}

	if recoveredSender != b.Sender {
		return fmt.Errorf("invalid searcher sig: expected %v, calculated %v", b.Sender.String(), recoveredSender.String())
	}

	return nil
}
```

**File:** kaiax/auction/bid.go (L132-134)
```go
func (b *Bid) Equals(other *Bid) bool {
	return b.BlockNumber == other.BlockNumber && b.TargetTxHash == other.TargetTxHash
}
```

**File:** kaiax/auction/impl/bid_pool.go (L345-394)
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
```
