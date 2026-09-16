### Title
Auction bid `MaxGasPrice` field is unauthenticated when the auction version defaults to V2 typehash, allowing a tampered bid to be accepted with an unsigned gas price cap - (File: kaiax/auction/eip712.go)

### Summary
`kaiax/auction`'s bid-signature verification chooses between two different EIP-712 struct type hashes (`auctionType` / `auctionTypeV3`) depending on a `version` string that mirrors the on-chain `AuctionEntryPoint.AUCTION_VERSION()`. The V3 type hash (`auctionTypeV3`) includes the `MaxGasPrice` field in the signed struct, but the V2 type hash (`auctionType`) does not. Critically, any version value that is neither `AuctionVersionV2` nor `AuctionVersionV3` ("unknown") silently falls back to the V2 (no-`MaxGasPrice`) type hash, exactly like the V2 path. This means `MaxGasPrice` is a field the searcher can never actually bind their signature to except when the module has definitively resolved the version to V3, even though `MaxGasPrice` is stored, hashed for RLP identity, and transported like any other authenticated bid field.

### Finding Description
`Bid.GetHashTypedData` builds the EIP-712 struct hash conditionally: [1](#0-0) 

```
if version == AuctionVersionV3 {
    structHash = EncodeEIP712(bidV3{b})
} else if version == AuctionVersionV2 {
    structHash = EncodeEIP712(b)
} else {
    // Unknown version: default to v2.1 typehash.
    structHash = EncodeEIP712(b)
}
```

`bidV3.EncodeData()` includes `MaxGasPrice` in the signed payload, while `Bid.EncodeData()` (used for both `AuctionVersionV2` and the "unknown" fallback) omits it entirely: [2](#0-1) 

`Bid.ValidateSearcherSig` simply recovers the signer from whatever digest `GetHashTypedData` produces and compares it to `b.Sender`: [3](#0-2) 

The `BidData` struct itself carries `MaxGasPrice` as a first-class, RLP-encoded, publicly-submittable field: [4](#0-3) 

This is functionally the same bug class as the referenced report: the report's protocol failed to require that the strategy discriminator (`BtcMangoFunding`/`SolMangoFunding` vs `BtcMangoMercurial`/`SolMangoMercurial`) matched the code path being executed, letting an attacker apply one strategy's accounting to another strategy's deposit — the underlying flaw is "a value-relevant field is trusted/used without being cryptographically bound to the correct discriminator/version." Here, the `MaxGasPrice` field is trusted and stored in the bid, but is only bound to the searcher's signature when the resolved auction version is unambiguously V3; for V2 deployments (and any unrecognized/未-yet-resolved version string) it is completely unauthenticated data that rides along with an otherwise validly-signed bid.

Any unprivileged party submitting bids via the public `auction_submitBid` RPC (`kaiax/auction/impl/api.go`'s `SubmitBid`, which decodes the caller-supplied `BidInput` into a `Bid` via `ToBid`) can therefore take a genuinely searcher-signed bid (whose signature is computed under the V2/"unknown" branch) and modify only the `MaxGasPrice` field before submission — the searcher signature check will still pass because that field was never part of the signed digest. Since `AddBid`/`insertBid` accept the bid based purely on `validateBid`/`validateBidSigs` (which only checks `ValidateSearcherSig`/`ValidateAuctioneerSig`, block-number range, non-zero bid, data size and gas-limit caps), the pool has no independent means to detect that `MaxGasPrice` was altered post-signature: [5](#0-4) 

### Impact Explanation
`MaxGasPrice` is designed to be a searcher-authorized ceiling on the gas price the bid transaction can be charged during settlement (it is explicitly the field added in the V3 typehash to close a prior gap: `"v3.0 selects the typehash that includes maxGasPrice"`, per the comment in `eip712.go`). If the ceiling can be silently modified without invalidating the searcher's own signature (whenever the resolved version is V2 or falls into the "unknown" default), a searcher's originally-authorized gas-price cap is not actually enforced by their own cryptographic consent — a third party (or a colluding block proposer/auctioneer) can raise `MaxGasPrice` on a bid before/at submission and have the pool accept it as if the searcher agreed to that higher ceiling. This directly maps to the report's "fee/settlement abuse" impact class: value that should require the searcher's authorization (their gas-price cap) can be altered and enforced against them without their consent, i.e., unauthorized fee/value exposure during auction settlement.

### Likelihood Explanation
The trigger condition (resolved auction version is `AuctionVersionV2` or any value other than `AuctionVersionV3`) is not a rare edge case — it is the default/fallback path and applies to any deployment or transition period where the on-chain `AuctionEntryPoint.AUCTION_VERSION()` is `"0.0.1"` or not yet correctly synchronized by `execution.go`'s registry-tracking logic. The action requires only submitting a bid via the standard, public `auction_submitBid` RPC with an unprivileged key — no special role, validator, or node access is needed.

### Recommendation
Make `MaxGasPrice` unconditionally part of the signed EIP-712 struct (i.e., always use the `bidV3`-style encoding that includes `MaxGasPrice`, defaulting its value to zero/absent only in the encoding, not by omitting the field from the hash), or explicitly reject/zero-out `MaxGasPrice` when the resolved version does not support it, so that no numeric value in a stored/transmitted `Bid` can ever be unauthenticated relative to the searcher's signature. This mirrors the referenced remediation of "explicitly require the [discriminator/strategy] to match the code path" rather than silently defaulting to a weaker semantic.

### Proof of Concept
1. Deploy/operate under an `AuctionEntryPoint` whose `AUCTION_VERSION()` resolves to `AuctionVersionV2` ("0.0.1") or to a value the module does not recognize (any string other than "0.0.1"/"0.0.2", e.g., during a version-registry transition).
2. A legitimate searcher signs a `Bid` with `MaxGasPrice = X` (or unset) using `GetHashTypedData(chainId, verifyingContract, "0.0.1")`; because the V2 branch is taken, the resulting digest and `SearcherSig` do not depend on `MaxGasPrice` at all.
3. An attacker intercepts or otherwise obtains this bid (e.g., from RPC broadcast/relayer visibility) and resubmits it via `auction_submitBid` with `MaxGasPrice` changed to `Y != X`.
4. `AuctionAPI.SubmitBid` → `ToBid` → `bidPool.AddBid` → `validateBid` → `validateBidSigs` calls `ValidateSearcherSig` with the same version, recomputes the same V2 digest (still excluding `MaxGasPrice`), and the signature check succeeds despite the field being tampered with.
5. The tampered bid is accepted into the pool and can be selected as the block's winning bid with an unauthenticated `MaxGasPrice` value. [6](#0-5) 

**Uncertainty note:** I was unable to fully inspect `kaiax/auction/impl/execution.go` (how `auctionEntryPointVersion` is resolved/updated across blocks) and `blockchain/system/auction.go` (how `MaxGasPrice` is actually consumed/enforced during on-chain settlement) before running out of tool calls. This limits certainty about (a) exactly how often/long a node operates in the "unknown version" fallback state in production, and (b) whether the on-chain `AuctionEntryPoint` contract itself provides an independent enforcement of `MaxGasPrice` that would mitigate the off-chain validation gap. These would need to be confirmed in a live session before treating this as fully proven end-to-end value theft rather than an authentication gap in the off-chain bid-pool signature check.

### Citations

**File:** kaiax/auction/eip712.go (L75-118)
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

// EncodeEIP712 encodes any EIP712Encoder according to EIP-712 specification
func EncodeEIP712(encoder EIP712Encoder) []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, encoder.EncodeType()...)
	encoded = append(encoded, encoder.EncodeData()...)
	return crypto.Keccak256Hash(encoded).Bytes()
}

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
