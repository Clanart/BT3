### Title
Auctioneer signature has no domain separator, allowing cross-chain/cross-auctioneer replay of bid approvals - (File: kaiax/auction/bid.go)

### Summary
The searcher's EIP-712 bid signature (`SearcherSig`) is correctly bound to `chainId`, `verifyingContract`, and a version string via `GetHashTypedData()`, but the auctioneer's approval signature (`AuctioneerSig`) is verified over a digest that only covers the raw `SearcherSig` bytes with a bare `"\x19Ethereum Signed Message:\n"` prefix, with no chain ID, contract address, or domain separator at all.

### Finding Description
`Bid.GetEthSignedMessageHash()` computes the digest the auctioneer is expected to have signed as `keccak256("\x19Ethereum Signed Message:\n" + len(SearcherSig) + SearcherSig)`, i.e. it signs only over the opaque `SearcherSig` byte blob, not the bid content and not any chain/contract-scoped domain separator: [1](#0-0) . This is then verified in `ValidateAuctioneerSig`, which recovers the signer from that domain-less digest and compares it to the configured `auctioneer` address, with no chain ID or verifying-contract check anywhere in this path: [2](#0-1) .

By contrast, `ValidateSearcherSig`/`GetHashTypedData` explicitly build an EIP-712 domain separator containing `chainId` and `verifyingContract` before hashing the bid struct: [3](#0-2) . The auctioneer-side check in `BidPool.validateBidSigs` calls `ValidateSearcherSig` with the pool's chain ID and entry point, then calls `ValidateAuctioneerSig` with only the configured `auctioneer` address — no chain/contract binding is passed or used: [4](#0-3) .

Because `SearcherSig` is a raw 65-byte ECDSA signature (deterministic function of the searcher's private key and the EIP-712 digest), and the auctioneer's approval is computed purely as a function of those same 65 bytes with no additional domain context, any auctioneer signature obtained for a `SearcherSig` produced under one deployment context (chain ID X, entry-point address A, version V) is trivially portable to any other bid pool/entry point that happens to accept the identical `SearcherSig` bytes as valid input — most importantly, if the same searcher key signs the *same* nonce/target/bid parameters against a different chain ID or a different `auctionEntryPoint`/version (all of which the auctioneer never sees or authenticates), an `AuctioneerSig` collected once could be replayed by an attacker relaying the pair `(SearcherSig, AuctioneerSig)` into a different auction pool instance (e.g. a different Kaia chain running the same auctioneer key, or after an entry-point contract upgrade/version bump changes `auctionEntryPointVersion`) without needing any additional auctioneer interaction, since `ValidateAuctioneerSig` performs no context check at all.

### Impact Explanation
`ValidateAuctioneerSig` is a mempool admission gate in `BidPool.validateBidSigs`, called before a bid is accepted into the bid pool and later executed on-chain via the auction entry point (`EncodeAuctionCallData`/`execution.go`) [5](#0-4) . If an `AuctioneerSig` can be replayed across bid-pool contexts (chain ID, entry point address, or protocol version changes) without new auctioneer consent, an attacker could get bids admitted and executed that the auctioneer never actually approved for that specific deployment, potentially causing unauthorized MEV/auction settlement execution and fee/bid extraction inconsistent with the auctioneer's intended approval scope — this directly implicates auction settlement integrity, a covered category (gasless and auction modules).

### Likelihood Explanation
Exploitation requires an attacker to obtain a `(SearcherSig, AuctioneerSig)` pair legitimately issued in one context and a second reachable bid-pool context (e.g. a redeployed/upgraded auction entry point, a version bump from `AuctionVersionV2` to `AuctionVersionV3`, or another chain sharing the same auctioneer key) that will accept the replayed pair; it does not require any privileged access and is reachable purely by submitting a bid via the public bid-submission path. Likelihood is moderate: it depends on operational reuse of auctioneer keys or entry-point redeployment/versioning, which is plausible in a system that already supports multiple `AuctionVersion` typehashes and rotating `auctionEntryPoint`/`auctioneer` values per `updateAuctionInfo`.

### Recommendation
Bind the auctioneer's signature to the same domain-scoped context as the searcher's, e.g. have the auctioneer sign over `keccak256(chainId || auctionEntryPoint || version || SearcherSig)` (or better, sign the full EIP-712 struct hash/domain used for the searcher, plus the `SearcherSig`), and verify that context inside `ValidateAuctioneerSig` (passing `chainId`, `verifyingContract`, and `version` in the same way `ValidateSearcherSig` does), so an approval cannot be replayed across chains, entry-point addresses, or protocol versions.

### Proof of Concept
1. Auctioneer legitimately signs off on a valid `SearcherSig` for bid B under bid-pool context C1 (chain ID X, `auctionEntryPoint` A1, version V1), producing `AuctioneerSig1 = sign(keccak256("\x19Ethereum Signed Message:\n65" + SearcherSig))` per `GetEthSignedMessageHash` [1](#0-0) .
2. Operator upgrades/redeploys the auction entry point (new address A2) or bumps `AUCTION_VERSION` from V1 to V2 while keeping the same auctioneer key, and calls `updateAuctionInfo` to refresh `auctionEntryPoint`/`auctionEntryPointVersion` [6](#0-5) .
3. Attacker resubmits the identical `Bid` (same `SearcherSig`, same `AuctioneerSig1`) against the new pool context via the public bid-submission API.
4. `validateBidSigs` recomputes `ValidateSearcherSig` against the new `chainId`/`auctionEntryPoint`/version (this step still passes only because the searcher signature happens to validate under whichever domain the caller supplies — but critically `ValidateAuctioneerSig` performs no domain check at all), so the pre-existing `AuctioneerSig1` is accepted unchanged since its digest never included any context [4](#0-3) , demonstrating the auctioneer approval carries no context binding and is replayable wherever the same `SearcherSig` bytes are reused/accepted.

### Citations

**File:** kaiax/auction/bid.go (L52-55)
```go
func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
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

**File:** kaiax/auction/impl/bid_pool.go (L196-213)
```go
func (bp *BidPool) updateAuctionInfo(auctioneer common.Address, auctionEntryPoint common.Address, auctionEntryPointVersion string, bidTxGasBuffer uint64) {
	bp.auctionInfoMu.Lock()
	defer bp.auctionInfoMu.Unlock()

	if bp.auctioneer == auctioneer && bp.auctionEntryPoint == auctionEntryPoint && bp.auctionEntryPointVersion == auctionEntryPointVersion && bp.bidTxGasBuffer == bidTxGasBuffer {
		return
	}

	// Clear the existing auction pool since the auctioneer or auction entry point address is changed.
	bp.clearBidPool()

	bp.auctioneer = auctioneer
	bp.auctionEntryPoint = auctionEntryPoint
	bp.auctionEntryPointVersion = auctionEntryPointVersion
	bp.bidTxGasBuffer = bidTxGasBuffer

	logger.Info("Update auction info", "auctioneer", auctioneer, "auctionEntryPoint", auctionEntryPoint, "auctionEntryPointVersion", auctionEntryPointVersion, "bidTxGasBuffer", bidTxGasBuffer)
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
