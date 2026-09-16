## Analog Found

### Title
Auction winning-bid selection fully trusts the offchain Auctioneer without onchain verification that it reflects the true highest bid - (File: kaiax/auction/impl/bid_pool.go)

### Summary
Kaia's KIP-249 auction module (`kaiax/auction`) delegates the entire determination of "who wins the right to append a transaction after a target tx" to an external, independent offchain service called the `Auctioneer`. The client-side `BidPool` only checks that a bid carries a cryptographically valid `AuctioneerSig`; it never verifies that the bid the Auctioneer chose to sign and submit is actually the highest (or otherwise correct) bid among all bids that searchers submitted for that block/target. This is structurally the same trust gap described in the external report: a value-affecting outcome (cutting-board weights / auction winner) is computed entirely offchain by a trusted role and accepted onchain (or by the node) without any onchain mechanism to prove the offchain computation matches the real inputs.

### Finding Description
The auction README explicitly documents this trust model: "All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`." [1](#0-0) 

Searchers submit bids through the public `auction_submitBid` RPC/API — an unprivileged, permissionless action [2](#0-1) . Those bids are held in the `BidPool`, keyed by `(blockNumber, sender)` and `(blockNumber, targetTxHash)` [3](#0-2) .

`validateBid` and `validateBidSigs` only verify structural/signature validity of an incoming bid (block-number range, positive bid amount, size/gas limits, and that `SearcherSig`/`AuctioneerSig` recover to the expected addresses) — there is no check that the auctioneer-selected bid is the genuinely highest bid submitted by all competing searchers for that slot: [4](#0-3) 

`insertBid` will only replace an existing target-tx bid if the *newly-submitted* bid (also signed by the same trusted Auctioneer) is strictly greater, but this comparison is scoped only to bids the Auctioneer chose to forward to this particular node — a node has no way to independently verify the Auctioneer actually surfaced the true highest bid from the full universe of searcher submissions: [5](#0-4) 

The signature checks (`ValidateSearcherSig`, `ValidateAuctioneerSig`) only prove *authenticity* (that the Auctioneer did sign this bid), not *correctness* (that this bid was the rightful winner): [6](#0-5) 

This is architecturally identical to the reported bug class: `Infrared.queueNewCuttingBoard()` trusted an offchain Keeper's representation of onchain voting outcomes without onchain verification; here, the Kaia CN trusts the offchain Auctioneer's representation of the "winning bid" among searcher submissions without any onchain/protocol-level verification connecting the claimed winner back to the actual competing bids.

### Impact Explanation
If the Auctioneer (due to bug, censorship, unfair tie-breaking, or flawed offchain matching logic) selects and signs a bid that is not the true top bid, a searcher who submitted a higher, valid bid loses their paid-for slot/MEV opportunity to a lower bidder, while the Auctioneer's selected winner captures value it did not rightfully win. Since bidding, ordering, and payment settlement of MEV opportunities directly move value between unprivileged users (searchers) and the block proposer/lender, an incorrect (non-malicious) auction outcome directly causes unauthorized value misallocation among searchers — analogous to "losses for specific ... receivers" in the original report.

### Likelihood Explanation
Low-to-Medium: the Auctioneer is a semi-trusted, permissioned, independent service (analogous to the "Keeper" role) and is assumed to run correctly-audited offchain logic; exploitation requires either an implementation bug, censorship, or an incentive to misreport, rather than an onchain adversary directly forging signatures. However, unlike a purely malicious-operator scenario, this is a design gap (no onchain verifiability of auction fairness) reachable purely through the standard, unprivileged `auction_submitBid` flow available to any searcher.

### Recommendation
Introduce an onchain (or cryptographically verifiable) mechanism that lets any observer/searcher verify that the Auctioneer's submitted winning bid was indeed the highest among all bids submitted for a given `(blockNumber, targetTxHash)`, e.g., by requiring the Auctioneer to commit to and reveal the full ordered bid set, or by having the `AuctionEntryPoint` contract itself enforce a minimum bid-vs-history check, rather than relying solely on `AuctioneerSig` authenticity as done in `validateBidSigs`.

### Proof of Concept
1. Multiple searchers submit competing bids (via `auction_submitBid`) targeting the same `targetTxHash` for block `N`, with Searcher A's bid strictly greater than Searcher B's bid.
2. The offchain Auctioneer, due to a bug/censorship/incorrect matching, selects Searcher B's (lower) bid, signs it, and forwards only that bid to the CN.
3. `BidPool.validateBid`/`insertBid` in `kaiax/auction/impl/bid_pool.go` accepts B's bid because it carries a valid `AuctioneerSig` and satisfies all local structural checks — there is no cross-check against A's genuinely higher bid, which the node never even receives.
4. Searcher B's transaction bundle is included and executed ahead of Searcher A's, even though A rightfully should have won and paid more — A's paid value/expected MEV capture is lost with no onchain recourse or verifiability.

### Citations

**File:** kaiax/auction/README.md (L7-7)
```markdown
The bid is a data that contains the information to generate a transaction to be executed right after the target transaction is executed. All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`.
```

**File:** kaiax/auction/README.md (L58-60)
```markdown
## APIs

### auction_submitBid
```

**File:** kaiax/auction/impl/bid_pool.go (L60-63)
```go
	bidMu        sync.RWMutex
	bidMap       map[common.Hash]*auction.Bid              // (bidHash) -> Bid
	bidTargetMap map[uint64]map[common.Hash]*auction.Bid   // (blockNum, targetTxHash) -> Bid
	bidWinnerMap map[uint64]map[common.Address]common.Hash // (blockNum, sender) -> bidHash
```

**File:** kaiax/auction/impl/bid_pool.go (L294-309)
```go
	// If same block number, same target tx hash exists, replace it if it's better
	if existingBid, ok := bp.bidTargetMap[blockNumber][targetTxHash]; ok {
		// FCFS if the bid is the same.
		if existingBid.Bid.Cmp(bid.Bid) >= 0 {
			return auction.ErrLowBid
		}

		logger.Trace("Replace bid", "old", existingBid.Hash(), "new", bid.Hash())
		delete(bp.bidMap, existingBid.Hash())
		delete(bp.bidWinnerMap[blockNumber], existingBid.Sender)
	} else {
		if int64(len(bp.bidMap)) >= bp.maxBidPoolSize {
			logger.Info("Bid pool is full", "maxBidPoolSize", bp.maxBidPoolSize, "bid", bid.Hash())
			return auction.ErrBidPoolFull
		}
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
