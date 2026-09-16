### Title
Version-dependent EIP-712 struct hash allows `MaxGasPrice` to be tampered without invalidating the auction bid's SearcherSig/AuctioneerSig - ([File: kaiax/auction/bid.go])

### Summary
The Kaia auction module (`kaiax/auction`) implements KIP-249 bid submission via `auction_submitBid`. A `Bid` carries a `MaxGasPrice` field, but whether that field is cryptographically committed to the searcher's EIP-712 signature depends entirely on the string-typed `version` parameter passed into `GetHashTypedData`. When the version is not exactly `"0.0.2"` (`AuctionVersionV3`) — including the declared `AuctionVersionV2` ("0.0.1") and any unrecognized version string — the struct hash is computed by `bid.EncodeData()`, which never serializes `MaxGasPrice` at all. This mirrors the borgbackup CVE-2023-36811 class of bug: data that is logically part of the authenticated object is not actually bound into the authentication tag, letting an attacker who can influence how the bid is processed forge/alter a field the signer never committed to while the "signature" still validates.

### Finding Description
`Bid.EncodeData()` in [1](#0-0)  deliberately omits `MaxGasPrice` from the EIP-712 struct encoding used for the v2.1 typehash (`auctionType`, which also has no `maxGasPrice` field in its type string, see [2](#0-1) ). Only `bidV3.EncodeData()` includes `MaxGasPrice` [3](#0-2) .

`GetHashTypedData` selects which encoding (and therefore which fields are authenticated) purely based on a caller-supplied `version` string: [4](#0-3) 
Critically, any version string other than the exact literal `"0.0.2"` — including a typo, an older deployed entry point, or simply the default `"0.0.1"` — silently falls back to the v2.1 encoding that has **no cryptographic binding to `MaxGasPrice`** whatsoever.

`ValidateSearcherSig` uses this same version-dependent digest to recover and check the searcher's signature [5](#0-4) . `ValidateAuctioneerSig` then only re-signs/verifies over the raw bytes of `SearcherSig` itself (`GetEthSignedMessageHash` = `keccak256("\x19Ethereum Signed Message:\n" + len(SearcherSig) + SearcherSig)`) [6](#0-5) [7](#0-6)  — it never re-hashes any bid field directly, so it inherits exactly whatever the searcher's signature happened to commit to.

The net effect: whenever the bid pool's configured `auctionEntryPointVersion` is `AuctionVersionV2` (`"0.0.1"`, the module's own constant/default, see [8](#0-7)  where `bp.auctionEntryPointVersion` is passed straight into `ValidateSearcherSig`), the entire `MaxGasPrice` value on the wire-level `Bid` struct is **not authenticated by either signature**. `validateBid`/`validateBidSigs` in the bid pool only checks `Bid > 0`, block-number range, data-size and gas-limit bounds, and the two signatures [9](#0-8)  — there is no separate check that `MaxGasPrice` is consistent with anything the searcher actually agreed to when v2.1 is in effect.

### Impact Explanation
Any component that reads `Bid.MaxGasPrice` after signature verification (e.g., to cap the gas price used when generating the winning bid transaction) will trust an attacker-controllable value once a v2.1-signed bid has passed `validateBid`. Since `MaxGasPrice` is not part of the searcher-authenticated struct hash under the fallback/v2.1 path, a party that can influence the `Bid` object between searcher signing and pool insertion — most plausibly the `Auctioneer` service itself, which constructs and re-signs bids submitted via `auction_submitBid`, or any relay resubmitting a searcher's original signed payload — can set/alter `MaxGasPrice` freely while both `ValidateSearcherSig` and `ValidateAuctioneerSig` still pass. This is a fee/authorization abuse vector: a searcher's bid could be executed with a gas price/cap the searcher never approved, causing unauthorized value movement from the searcher's declared preferences, or allow bid manipulation that the KIP-249 signature scheme is specifically supposed to prevent. This matches the "fee or fee-delegation abuse" / "auction settlement theft" impact categories in scope.

### Likelihood Explanation
The condition triggers whenever the configured/default auction version is `"0.0.1"` (the module's own `AuctionVersionV2` constant) rather than `"0.0.2"`. Since `AuctionVersionV2` is the module's baseline/default version and the `else` branch in `GetHashTypedData` silently degrades any unexpected version string to the same unauthenticated encoding, this is not a rare edge case — it is the default behavior path for the module unless the deployment is specifically pinned to the v3.0 entry point. No privileged access is required beyond the ability to submit or relay a bid through `auction_submitBid`, which is explicitly an in-scope caller role ("auction bidder").

### Recommendation
Make `MaxGasPrice` a mandatory, version-independent field in the EIP-712 struct hash (i.e., always include it in `EncodeData`, and use one canonical `AuctionTx` typehash that includes `maxGasPrice`), or explicitly reject/zero-fill and reject any non-empty `MaxGasPrice` when the negotiated version does not include it, so a searcher's commitment always covers every field the system later trusts. Additionally, have `ValidateAuctioneerSig` bind the auctioneer's signature to a hash of the full `BidData` (or at least all fields consumed downstream, including `MaxGasPrice`) rather than only to the opaque `SearcherSig` bytes, closing the same "insufficiently bound authentication" gap at the auctioneer layer.

### Proof of Concept
1. Deploy/operate an `AuctionEntryPoint` reporting `AUCTION_VERSION = "0.0.1"` (or any string ≠ `"0.0.2"`), which is the module's baseline (`AuctionVersionV2`).
2. A searcher builds a `Bid` with `MaxGasPrice = X` and signs it: `digest := bid.GetHashTypedData(chainId, entryPoint, "0.0.1")`, `sig := crypto.Sign(digest, searcherKey)` — matching the flow in [10](#0-9) . Note `EncodeData()` for this version never serializes `MaxGasPrice`.
3. Before the bid reaches `BidPool.validateBid`, an intermediary (e.g., the auctioneer relay) changes `bid.MaxGasPrice` to `Y != X`, then signs `AuctioneerSig` over `GetEthSignedMessageHash()`, which only hashes the unchanged `SearcherSig` bytes [6](#0-5) .
4. `bp.validateBidSigs` calls `ValidateSearcherSig` (recomputes the same v2.1 digest — unaffected by the `MaxGasPrice` change, so it still recovers `Sender`) and `ValidateAuctioneerSig` (recomputes hash of `SearcherSig` bytes — also unaffected) [8](#0-7) ; both checks pass despite `MaxGasPrice` having been altered to `Y`.
5. The tampered `Bid.MaxGasPrice = Y` is now accepted into the bid pool as if the searcher had approved it, even though the searcher's EIP-712 signature never covered that value.

### Citations

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

**File:** kaiax/auction/eip712.go (L139-147)
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

**File:** kaiax/auction/impl/bid_pool.go (L345-419)
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

**File:** kaiax/auction/bid_test.go (L48-51)
```go
func TestBidEIP712Encode(t *testing.T) {
	digest := testBid.GetHashTypedData(big.NewInt(31337), common.HexToAddress("0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9"), AuctionVersionV2)
	require.Equal(t, common.Hex2Bytes("da9b3f7a46d0b5e6875970b19ef7c60e2f969e5b44f6a4701b9889694df6fe0d"), digest)
}
```
