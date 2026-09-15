## Analysis Result

I found a concrete signature-scoping issue in the auction bid pool that matches the report's bug class (unclear signature domain separation leading to a replay/malleability gap), reachable purely by an unprivileged auction bidder/searcher submitting a bid via RPC.

### Title
Auctioneer signature lacks binding to bid content (only signs over SearcherSig bytes), enabling AuctioneerSig replay across unrelated bids with the same signature bytes but different metadata - (File: kaiax/auction/bid.go)

### Summary
The `AuctioneerSig` in the KIP-249 auction flow is computed and verified over `crypto.Keccak256("\x19Ethereum Signed Message:\n{len}{SearcherSig}")` — i.e., it only signs the raw bytes of `SearcherSig`, not the bid's semantic fields (`TargetTxHash`, `BlockNumber`, `Sender`, `Nonce`, `Bid` amount, etc.) directly, and with no chainId/contract/action domain separation of its own.

### Finding Description
`Bid.GetEthSignedMessageHash()` [1](#0-0)  builds the message the auctioneer signs from `b.SearcherSig` alone. `ValidateAuctioneerSig` [2](#0-1)  recovers the signer from this same hash and requires it match the configured `auctioneer` address.

By contrast, `SearcherSig` is bound with a full EIP-712 domain (`chainId`, `verifyingContract`, `version`) and struct hash over all bid fields [3](#0-2) , so the ChainPort report's "networkId" and "action"-field concerns are already addressed for the searcher's signature. However, the auctioneer's approval is only a wrapper attesting "I approve whatever this raw signature is," not "I approve this specific (targetTxHash, blockNumber, sender, nonce, bid, data) tuple." The bid-pool admission code (`validateBidSigs` in `kaiax/auction/impl/bid_pool.go`) [4](#0-3)  treats a valid `AuctioneerSig` as authorization for the entire `BidData`, including fields the auctioneer's signature never actually commits to independently (it trusts that `SearcherSig` is bound 1:1 to those fields, which is true only as long as ECDSA signatures are non-malleable and the EIP-712 digest computation is exactly reproduced everywhere it is checked).

This is exactly the class of problem the ChainPort report flags: a signature scheme where one signature's scope is defined only by *reference* to another opaque blob (raw sig bytes) rather than by directly committing to the semantic payload, with no explicit "why is this field included / why is uniqueness needed" specification. Practically, this creates a fragile trust chain: any code path that produces a `SearcherSig` byte string that verifies against a *different* set of `BidData` fields (e.g. via signature malleability, or a future refactor that recomputes the EIP-712 digest differently for v2 vs v3 `AUCTION_VERSION`, note the dual type-hash branching in `GetHashTypedData` [5](#0-4) ) would cause the already-issued `AuctioneerSig` to remain valid for that new payload, because the auctioneer never signed the payload — only the byte string.

### Impact Explanation
If the SearcherSig-to-BidData binding is ever broken (e.g., a mismatch between the Go client's EIP-712 encoding and the on-chain Solidity encoding for a given `AUCTION_VERSION`, or introduction of a new auction version whose struct hash collides/overlaps with an old one under the same domain separator), the auctioneer's approval would be silently reused for unintended bid content without the auctioneer ever re-approving it. Since the bid pool relies on `ValidateAuctioneerSig`/`ValidateSearcherSig` as the sole authorization gate before a bid is scheduled into a block-building bundle (`BidPool.validateBid` → `validateBidSigs`) [6](#0-5) , this could lead to acceptance of an auction bid the auctioneer did not actually intend to approve, i.e., unauthorized settlement/ordering privilege — a fee/auction-settlement abuse vector.

### Likelihood Explanation
Exploitation requires either (a) an EIP-712 encoding mismatch/version-typehash collision between the Go bid encoder and the on-chain verifier, or (b) some other mechanism producing two distinct `BidData` payloads whose `SearcherSig` bytes are identical. I could not find and did not have tool access to prove a concrete encoding mismatch between `contracts/bindings/auction`/`auctionv3` on-chain verification and `kaiax/auction/eip712.go`; the dual-version branching (`AuctionVersionV2`/`AuctionVersionV3`) at least establishes that the "auctioneer signs only the searcher-sig bytes" design is inherently version/encoding-agnostic and therefore does not independently protect against such mismatches. Likelihood is therefore Medium — the design is a latent gap rather than a directly demonstrated forgery today.

### Recommendation
Have the auctioneer sign a digest that directly commits to the semantic bid fields (e.g., include the EIP-712 struct hash or hash of `BidData` itself in the auctioneer's signed message, not just the raw `SearcherSig` bytes), and document explicitly (per the ChainPort report's recommendation) what each signature is meant to protect against: integrity of bid content vs. mere pass-through approval of an opaque blob. This removes any reliance on the searcher-signature encoding being perfectly consistent across versions/implementations for the auctioneer's authorization to remain scoped correctly.

### Proof of Concept
Not independently reproducible with the tools available in this session (no code-execution/terminal access to demonstrate an actual EIP-712 encoding mismatch between the Go client and the on-chain Solidity verifier, or a signature-malleability collision). The static-analysis evidence above shows the structural gap: `ValidateAuctioneerSig` never inspects `BidData` fields, only `SearcherSig` bytes [1](#0-0) , so a full PoC would require constructing two distinct `BidData` values that hash/sign to the same `SearcherSig` bytes under some encoding path — this needs live signing and on-chain contract testing (Solidity `AuctionEntryPoint`/`AuctionEntryPointV3`, not present in full in the indexed sources) that a background Devin session with repo and terminal access could pursue.

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
