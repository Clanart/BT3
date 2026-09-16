### Title
Auctioneer signature is bound only to searcher-signature bytes, not to the bid content itself — ([File: kaiax/auction/bid.go])

### Summary
The auction module's `ValidateAuctioneerSig` verifies the auctioneer's signature over a hash of the `SearcherSig` bytes only, rather than over the bid's actual content. Combined with the version-based EIP-712 typehash selection (V2 struct omits `maxGasPrice`), a bid field (`MaxGasPrice`) can be varied without invalidating either signature when the pool is running under the V2 typehash, breaking the intended one-commitment-per-bid binding — conceptually the same class of defect as the UMA report: a signed/committed artifact that does not fully and uniquely bind to all the data it is supposed to authorize.

### Finding Description
`Bid.ValidateSearcherSig` computes an EIP-712 digest from the bid fields and checks that `SearcherSig` recovers to `b.Sender`: [1](#0-0) 

`Bid.ValidateAuctioneerSig`, however, does **not** hash the bid data at all. It hashes only the raw `SearcherSig` bytes via `GetEthSignedMessageHash`, and checks that the auctioneer's signature recovers over that: [2](#0-1) [3](#0-2) 

The EIP-712 struct used for `SearcherSig` depends on a *string* version parameter supplied at validation time (`AuctionVersionV2` = `"0.0.1"` vs `AuctionVersionV3` = `"0.0.2"`), and the V2 typehash/struct intentionally excludes `MaxGasPrice`: [4](#0-3) [5](#0-4) 

Because `BidData.MaxGasPrice` is a first-class field of the `Bid` struct sent over RPC (`kaiax/auction/bid.go` lines 32-44) but is not part of the V2 EIP-712 digest, a `SearcherSig` created under version V2 is valid for **any** value of `MaxGasPrice` — the field can be freely altered after signing without affecting `ValidateSearcherSig`. Since `ValidateAuctioneerSig` only re-hashes the (unchanged) `SearcherSig` bytes, it also remains valid. The only field that is bound to `AuctioneerSig` at all is the literal byte-string of `SearcherSig`, not the semantic bid content, so any bid content not covered by the active EIP-712 typehash is effectively "commitment-free" and can be tampered with while both signatures still verify — mirroring the audit's underlying defect class: a commitment/signature that does not cryptographically cover all the material facts it is meant to authorize.

The pool's `bid.Hash()` (used for the `ErrBidAlreadyExists` de-dup check) is computed over the full `BidData` including `MaxGasPrice`, so this is not caught by the duplicate-hash check: [6](#0-5) [7](#0-6) 

### Impact Explanation
If the deployed `AuctionEntryPoint`/auctioneer flow is configured (or ever falls back, per the "Unknown version" branch in `GetHashTypedData`) to the V2 typehash while `MaxGasPrice` is present and meaningfully consumed downstream (e.g., gas price ceiling enforcement for the bid/lend transaction bundle), an attacker who observes a valid `(SearcherSig, AuctioneerSig)` pair for a bid can rebroadcast a modified `Bid` object with a different `MaxGasPrice` and have it pass both `ValidateSearcherSig` and `ValidateAuctioneerSig`, since neither signature actually commits to that field under V2. This could let an unprivileged party manipulate the effective gas-price constraint of a bid that the searcher never actually authorized, i.e., unauthorized modification of a purportedly signed/committed auction parameter — analogous to the UMA vote-duplication class where a commitment fails to bind all relevant context.

### Likelihood Explanation
Likelihood depends entirely on runtime configuration: if `auctionEntryPointVersion` is fixed to V3 (`"0.0.2"`) in production, `MaxGasPrice` is included in the digest and this gap does not manifest. The vulnerability requires the V2 code path (or the undefined "unknown version" fallback, which also defaults to the V2 encoder per `GetHashTypedData`) to be reachable while `MaxGasPrice` is populated and used meaningfully by the entry point/execution logic. Whether the codebase's current production configuration and on-chain `AuctionEntryPoint.AUCTION_VERSION()` ever route through V2 with a non-zero `MaxGasPrice` in effect could not be fully confirmed from the available index; this determines actual exploitability and is noted as an open verification item.

### Recommendation
- Make `ValidateAuctioneerSig` bind directly to the bid's content (e.g., hash `bid.Hash()` or the same EIP-712 digest used for the searcher, rather than hashing only the `SearcherSig` bytes), so a tampered field cannot survive re-validation merely because the underlying searcher signature bytes are unchanged.
- Ensure every field that can influence execution/economic outcome (`MaxGasPrice`, `CallGasLimit`, `Data`, etc.) is always included in the EIP-712 struct that is actually used for signature verification, regardless of version, and remove the silent "unknown version defaults to V2" fallback in `GetHashTypedData`.
- Add a required, cryptographically-checked `AuctionVersion` field to the signed struct itself, so the choice of typehash cannot be manipulated independently of the signed data.

### Proof of Concept
1. Configure/observe the pool with `auctionEntryPointVersion = AuctionVersionV2` (`"0.0.1"`), matching `kaiax/auction/impl/bid_pool.go` line 182 in tests (`pool.auctionEntryPointVersion = auction.AuctionVersionV2`).
2. A searcher signs a legitimate bid `B1` with `MaxGasPrice = nil/0` via `GetHashTypedData(chainId, entryPoint, AuctionVersionV2)`; the auctioneer signs `GetEthSignedMessageHash()` over `B1.SearcherSig`.
3. An attacker copies `B1`, sets `MaxGasPrice` to an arbitrary large or small value producing `B2`, keeping `SearcherSig` and `AuctioneerSig` unchanged.
4. Submit `B2` via `auction_submitBid`. `ValidateSearcherSig` recomputes the V2 digest, which excludes `MaxGasPrice`, so it is identical to `B1`'s digest and still recovers to `Sender`; `ValidateAuctioneerSig` re-hashes the unchanged `SearcherSig` bytes and still recovers to `auctioneer`. `bid.Hash()` differs from `B1`'s hash (since `MaxGasPrice` is part of `BidData`), so `ErrBidAlreadyExists` does not block it — `B2` is accepted with an attacker-chosen `MaxGasPrice` that was never actually signed by the searcher for that value.

### Citations

**File:** kaiax/auction/bid.go (L52-55)
```go
func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
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

**File:** kaiax/auction/bid.go (L117-130)
```go
func (b *Bid) ValidateAuctioneerSig(auctioneer common.Address) error {
	digest := b.GetEthSignedMessageHash()

	recoveredAuctioneer, err := getSigner(b.AuctioneerSig, digest)
	if err != nil {
		return fmt.Errorf("failed to recover auctioneer sig: %v", err)
	}

	if recoveredAuctioneer != auctioneer {
		return fmt.Errorf("invalid auctioneer sig: expected %v, calculated %v", auctioneer.String(), recoveredAuctioneer.String())
	}

	return nil
}
```

**File:** kaiax/auction/eip712.go (L26-43)
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

var (
	auctionTypeHash   = crypto.Keccak256Hash([]byte(auctionType))
	auctionTypeHashV3 = crypto.Keccak256Hash([]byte(auctionTypeV3))
	eip712TypeHash    = crypto.Keccak256Hash([]byte(EIP712DomainType))
	auctionNameHash   = crypto.Keccak256Hash([]byte(auctionName))
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

**File:** kaiax/auction/impl/bid_pool.go (L345-392)
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
```
