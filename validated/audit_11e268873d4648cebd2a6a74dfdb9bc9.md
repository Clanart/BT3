## Title
Auctioneer signature does not bind to bid content, allowing post-approval tampering of unsigned bid fields (e.g. `MaxGasPrice`) — analog of Cosign's unbound bundle signature - (File: `kaiax/auction/bid.go`)

### Summary
The Cosign advisory's Vulnerability 1 describes a "bundle" composed of two independently-verified artifacts (signature+cert, and a rekorBundle) where the second artifact is validated in isolation without confirming it actually references the first. Kaia's auction module (`kaiax/auction`) has the same structural flaw: a `Bid` carries two signatures, `SearcherSig` and `AuctioneerSig`, that are validated independently by `BidPool.validateBidSigs`, but `AuctioneerSig` never actually commits to the bid's content.

### Finding Description
`ValidateAuctioneerSig` recovers the signer from a digest computed purely over the `SearcherSig` bytes: [1](#0-0) [2](#0-1) 

This means the auctioneer's approval is only ever an attestation of "I have seen this exact `SearcherSig` byte blob" — it never binds to `TargetTxHash`, `BlockNumber`, `Sender`, `To`, `Nonce`, `Bid`, `CallGasLimit`, `Data`, or `MaxGasPrice`.

Content-binding is expected to come entirely from `SearcherSig`, via the EIP-712 digest built in `ValidateSearcherSig`/`GetHashTypedData`: [3](#0-2) [4](#0-3) 

However, the V2/v2.1 struct typehash (`auctionType`) used for `AuctionVersionV2` does **not** include `MaxGasPrice` in its encoded data — only the V3 typehash (`auctionTypeV3`) does: [5](#0-4) [6](#0-5) [7](#0-6) 

`BidData.MaxGasPrice` is a normal mutable field on the `Bid` struct that is fully populated on `AddBid` and later used when the proposer encodes the actual on-chain calldata via `EncodeAuctionCallData` if the entry-point's active version is V3: [8](#0-7) [9](#0-8) 

Because `bp.validateBidSigs` verifies signatures using `bp.auctionEntryPointVersion` (the *currently active* on-chain version at bid-submission time) rather than binding the signature to a version that is immutable per-bid, and because neither signature layer ever covers `MaxGasPrice` under the V2 typehash: [10](#0-9) 

an attacker (the bidder/searcher itself, or anyone relaying a bid they intercept) can set or alter `bid.MaxGasPrice` on a `Bid` object that was signed under the V2 EIP-712 digest and still pass both `ValidateSearcherSig` (digest excludes `MaxGasPrice`) and `ValidateAuctioneerSig` (digest is only over `SearcherSig` bytes, never touches any bid field). If the entry point subsequently reports/transitions to V3 (or if a race occurs around a version transition captured in `updateAuctionInfo`), the tampered `MaxGasPrice` is faithfully encoded into the executed on-chain calldata via `EncodeAuctionCallData`, even though neither the searcher nor the auctioneer ever attested to that value.

This is the same root cause pattern as the Cosign bug: a two-layer proof scheme (searcher signature + auctioneer signature) where the outer/"bundle" layer (`AuctioneerSig`) is verified without cross-checking that it actually references the full content of the inner artifact.

### Impact Explanation
An unprivileged auction bidder/searcher (or any relayer of a captured bid) can mutate `MaxGasPrice` — a field intended to cap the fee a searcher authorizes — after both required signatures have already been produced, without invalidating either signature check enforced by `BidPool.validateBidSigs`. Since `MaxGasPrice` flows directly into the generated auction bid transaction's execution parameters (`EncodeAuctionCallData` → `IAuctionEntryPointAuctionTx`), this can result in acceptance of a bid whose enforced fee/gas cap differs from what the searcher and auctioneer actually approved, undermining the auction's fee/gas guarantees and enabling settlement manipulation (CWE-347: Improper Verification of Cryptographic Signature).

### Likelihood Explanation
The proof itself is deterministic and requires no cryptographic breaking: it is a direct consequence of the auctioneer digest never depending on bid content, and of the V2 typehash omitting `MaxGasPrice` from the searcher's own digest. Any bidder able to submit bids via `auction_submitBid` can construct such a bid without any privileged access, provided they can obtain one legitimately auctioneer-approved `(SearcherSig, AuctioneerSig)` pair for their own bid (a normal part of the intended workflow).

### Recommendation
Bind `AuctioneerSig` to the full bid content (e.g., sign over the bid's own EIP-712 digest or its RLP hash) rather than over `SearcherSig` bytes alone, and ensure every `BidData` field intended to affect execution (including `MaxGasPrice`) is included in the EIP-712 typehash for every supported version, or reject/ignore fields not covered by the active version's typehash.

### Proof of Concept
1. As a searcher, build a V2 bid with `MaxGasPrice = nil` (or 0) and compute `SearcherSig` over `GetHashTypedData(chainId, entryPoint, AuctionVersionV2)` — this digest does not include `MaxGasPrice`.
2. Submit for/obtain a legitimate `AuctioneerSig` over `GetEthSignedMessageHash()` (which only hashes `SearcherSig` bytes) — see `ValidateAuctioneerSig` at `kaiax/auction/bid.go:117-130`.
3. After receiving both signatures, set `bid.MaxGasPrice` to an arbitrary large/small value.
4. Call `AddBid`/`auction_submitBid` with this bid: `validateBidSigs` (`kaiax/auction/impl/bid_pool.go:397-419`) still passes both `ValidateSearcherSig` and `ValidateAuctioneerSig` because neither digest is a function of `MaxGasPrice`.
5. If/when `EncodeAuctionCallData` is invoked with `AuctionVersionV3` (`blockchain/system/auction.go:83-103`), the tampered `MaxGasPrice` is faithfully packed into the on-chain call despite never having been attested to by either signer.

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

**File:** kaiax/auction/bid.go (L52-55)
```go
func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
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

**File:** kaiax/auction/eip712.go (L26-28)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
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

**File:** kaiax/auction/eip712.go (L120-149)
```go
// GetHashTypedData returns the EIP-712 digest for the bid.
// version must match the on-chain AUCTION_VERSION of the entry-point contract
// the bid targets; "0.0.2" selects the v3.0 struct typehash (with maxGasPrice),
// any other value falls back to the v2.1 typehash.
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

**File:** blockchain/system/auction.go (L79-103)
```go
// EncodeAuctionCallData encodes the bid as calldata for the active auction
// entry-point contract. version must match the on-chain AUCTION_VERSION of the
// active contract; "0.0.2" selects the v3.0 ABI (with maxGasPrice), any other
// value falls back to the v2.1 ABI.
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
