### Title
Auctioneer approval signature over MEV bids omits chainId/domain binding, enabling cross-chain/fork replay of admitted auction bids - ([File: kaiax/auction/bid.go])

### Summary
Kaia's auction (MEV) module admits searcher `Bid`s into the node's bid pool only after two signatures are validated: the searcher's EIP-712 signature and the auctioneer's approval signature. The searcher's signature is properly domain-separated with `chainId` and `verifyingContract` via EIP-712 (`GetHashTypedData`), but the auctioneer's approval signature is computed with `GetEthSignedMessageHash()`, a plain `personal_sign`-style hash over only the raw `SearcherSig` bytes, with **no chainId, no verifyingContract, and no auction-specific domain separator**. This mirrors the reported bug class (signature verification missing `chainId`, exploitable especially on a chain fork).

### Finding Description
`Bid.ValidateAuctioneerSig` recovers the auctioneer address from a digest produced by `GetEthSignedMessageHash`: [1](#0-0) 

That digest has no chain or contract binding, unlike the searcher's digest which is explicitly domain-separated by `chainId` and `verifyingContract`: [2](#0-1) [3](#0-2) 

Both signatures are checked together during bid admission into the pool, gating whether the bid (and its resulting on-chain settlement call) is accepted: [4](#0-3) 

Because the auctioneer's signature carries no chain-specific context, if the network experiences a chain fork (the scenario explicitly called out in the source report) where both resulting chains temporarily retain the same `chainId`, `auctionEntryPoint`, `auctionEntryPointVersion`, and `auctioneer` address (all read from governance/system contract state that has not yet diverged post-fork), an entire captured `Bid` object — SearcherSig, AuctioneerSig, and BidData together — remains valid on **both** forked chains. An attacker (or simply a node relaying gossip, or any RPC caller who resubmits a previously observed bid) can take a bid gossiped/observed on chain A and submit it to chain B's node via the p2p bid-gossip path (`BidPool.HandleBid`) or any bid-submission entry point, and it will pass `validateBidSigs` and be admitted into chain B's pool, since `ValidateSearcherSig` only checks chainId equality (which still holds immediately post-fork) and `ValidateAuctioneerSig` checks nothing chain-specific at all.

The searcher's own EIP-712 domain does bind `chainId`, so cross-chain replay across networks with genuinely different chain IDs is blocked at the `ValidateSearcherSig` step. The residual risk is specifically the fork scenario (matching the external report's explicit caveat: "This can happen when there is also a fork in the chain") where chainId has not yet diverged, and more generally that the auctioneer's approval — which is the operator-controlled admission gate — provides zero domain separation of its own, making it strictly weaker than intended and inconsistent with the searcher-side EIP-712 protection.

### Impact Explanation
If exploited during/after a fork where chain state (chainId, auctioneer, entry point) has not yet diverged, a previously-approved bid can be replayed onto the sibling chain without any new authorization from the auctioneer, causing the auction entry point contract to execute the bid's calldata and settle the bid amount on a chain the auctioneer never intended to authorize. This is unauthorized settlement/value movement outside the operator's control — a Medium/High severity issue for the auction/MEV subsystem, since it undermines the auctioneer's role as the sole gate for bid admission.

### Likelihood Explanation
Exploitation requires a fork event where chainId, `auctioneer`, and `auctionEntryPoint`/version have not yet diverged between the two networks — a narrow but realistic window immediately after a contentious fork before governance parameters are updated. Any party that can observe a gossiped bid (any peer) or resubmit via the bid-relay path can attempt the replay; no special privilege is needed beyond passive observation of bid traffic.

### Recommendation
Bind the auctioneer's approval signature to the same domain as the searcher's signature — include `chainId`, `verifyingContract` (auction entry point), and the bid's own EIP-712 struct hash (not just the raw `SearcherSig` bytes) in `GetEthSignedMessageHash`/`ValidateAuctioneerSig`, e.g. by having the auctioneer sign `keccak256(chainId || auctionEntryPoint || bid.GetHashTypedData(...))` rather than signing only over the searcher's raw signature bytes.

### Proof of Concept
1. Auctioneer approves a `Bid` (SearcherSig + AuctioneerSig) targeting chain A, block N, on entry point `E` with chainId `C`. [4](#0-3) 
2. A chain fork occurs producing chain B, which momentarily still reports the same `ChainConfig.ChainID = C`, the same `auctioneer`, and the same `auctionEntryPoint`/version (read via `updateAuctionInfo` from system contract state that hasn't diverged yet). [5](#0-4) 
3. An observer relays the identical `Bid` object to a chain-B node via `BidPool.HandleBid`. [6](#0-5) 
4. `validateBidSigs` on chain B recomputes `ValidateSearcherSig` (passes, chainId still matches) and `ValidateAuctioneerSig` (passes, since the auctioneer digest never included chainId/contract to begin with) — the bid is admitted and later included/settled on chain B without any auctioneer authorization specific to chain B.

Note: I was not able to fully verify the exact external RPC/gossip entry points beyond `BidPool.HandleBid` and `kaiax/auction/impl/api.go` (SubmitBid), so the precise transport used by a "public caller" to inject a replayed bid should be confirmed by the engineer picking this up.

### Citations

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

**File:** kaiax/auction/eip712.go (L120-150)
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
}
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

**File:** kaiax/auction/impl/bid_pool.go (L421-437)
```go
func (bp *BidPool) HandleBid(peerID string, bid *auction.Bid) {
	if atomic.LoadUint32(&bp.running) == 0 || bid == nil {
		return
	}

	// Check rate limit for this peer
	if !bp.checkRateLimit(peerID) {
		logger.Trace("Rate limit exceeded for peer", "peerID", peerID)
		return
	}

	select {
	case bp.bidMsgCh <- bid:
	default:
		logger.Trace("Bid queue is full, dropping bid", "peerID", peerID)
	}
}
```
