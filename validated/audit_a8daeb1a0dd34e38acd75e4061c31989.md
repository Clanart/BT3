Found a concrete analog: the auctioneer signature in the Kaia auction/bid module does not bind to `chainId` at all, unlike the searcher's EIP-712 signature which correctly includes `chainId` in its domain separator.

### Title
Auctioneer signature in `Bid.ValidateAuctioneerSig` lacks chainID / domain binding, enabling cross-chain bid replay - ([File: kaiax/auction/bid.go])

### Summary
`Bid.ValidateSearcherSig` correctly binds the searcher's signature to `chainId` and `verifyingContract` via an EIP-712 typed-data digest [1](#0-0) , using the EIP712Domain struct that embeds `ChainId` [2](#0-1) . However, `ValidateAuctioneerSig` signs/verifies only a raw `personal_sign`-style hash over the searcher's signature bytes — `GetEthSignedMessageHash()` — with no chain ID, no domain separator, and no reference to the target block, target tx hash, or bid content at all [3](#0-2) [4](#0-3) .

### Finding Description
The auctioneer's signature is computed as `keccak256("\x19Ethereum Signed Message:\n" + len(searcherSig) + searcherSig)` — i.e., it signs only the byte-string of the searcher's own signature, with no chainId, verifyingContract, or auction/version domain separator mixed in [3](#0-2) . This is the same class of bug as the ERC20Permit report: a signature schema omitting `chainID` from the signed payload.

`BidPool.validateBidSigs` calls both `ValidateSearcherSig` (chain-bound) and `ValidateAuctioneerSig` (not chain-bound) as the two required signature checks for admitting a bid into the pool [5](#0-4) . Because the auctioneer signature is only a function of the `SearcherSig` bytes (not the bid's `TargetTxHash`, `BlockNumber`, `Sender`, chainId, etc.), any auctioneer approval signature produced on one Kaia chain (e.g. testnet, or the chain before a hard fork/chain split) is valid on any other chain that shares the same auctioneer key and where a searcher happens to produce (or replays) the same `SearcherSig` bytes. More critically, since the auctioneer digest doesn't depend on chainId at all, it is trivially replayable across chain forks post-split — exactly the "Lack of chainID" bug class from the external report.

### Impact Explanation
Bid admission (`validateBidSigs`) gates whether a searcher's bid enters the `BidPool` and can ultimately win the block's auction, redirecting MEV/priority execution rights and associated fees to the searcher [5](#0-4) . If the auctioneer's authorization signature can be replayed across chains (post chain-split) or is not properly bound to the specific bid content/chain context, a malicious searcher or third party could reuse a previously-obtained valid `(SearcherSig, AuctioneerSig)` pair in a different chain context to get bids admitted without fresh auctioneer approval, potentially enabling unauthorized auction settlement / theft of auction proceeds or fee redirection on a forked/side network.

### Likelihood Explanation
Likelihood is Medium: it requires either a chain split/fork (same as the original report's exploit scenario) or an environment where the same auctioneer key operates across multiple chainIds (testnet/mainnet, or a hard-fork event as described in the original bug), plus a searcher signature that can be reused as-is. This is a plausible but not everyday occurrence, similar to the original report's exploit scenario requiring a hard fork.

### Recommendation
Short term: include `chainId` (and ideally the full bid content hash / auction round identifier) in the auctioneer's signed digest, analogous to the EIP-712 domain already used for the searcher signature, e.g. reuse `GetHashTypedData` or a chain-bound domain separator for the auctioneer sig instead of the bare `personal_sign` hash of `SearcherSig`.

Long term: unify both signature schemes under the same EIP-712 domain (including chainId and verifyingContract) so that both searcher and auctioneer approvals are cryptographically bound to the same chain and bid content, preventing any cross-chain or cross-context replay.

### Proof of Concept
1. Auctioneer signs `AuctioneerSig` over `GetEthSignedMessageHash()` = `keccak256("\x19Ethereum Signed Message:\n65" + SearcherSig)` on chain A (chainId=X) for a given bid [3](#0-2) .
2. After a chain split/fork event (or on a chain B sharing the same auctioneer key with chainId=Y), a searcher submits a `Bid` with the identical `SearcherSig` bytes (bytes are independent of chainId only if the searcher's own EIP-712 sig were also replayed, but the auctioneer sig alone imposes no chain check).
3. `BidPool.validateBidSigs` on chain B calls `ValidateAuctioneerSig(bp.auctioneer)`, which recomputes the same `GetEthSignedMessageHash()` and recovers the same auctioneer address, since chainId plays no role in this digest [4](#0-3) .
4. The bid is accepted into `bidMap`/`bidTargetMap` without the auctioneer having freshly authorized it on chain B, allowing bid/auction settlement fraud on the forked network.

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

**File:** kaiax/auction/eip712.go (L50-69)
```go
type EIP712Domain struct {
	EIP712DomainTypeHash common.Hash
	NameHash             common.Hash
	VersionHash          common.Hash
	ChainId              *big.Int
	VerifyingContract    common.Address
}

func (d EIP712Domain) EncodeType() []byte {
	return eip712TypeHash.Bytes()
}

func (d EIP712Domain) EncodeData() []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, d.NameHash.Bytes()...)
	encoded = append(encoded, d.VersionHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(d.ChainId.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(d.VerifyingContract.Bytes(), 32)...)
	return encoded
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
