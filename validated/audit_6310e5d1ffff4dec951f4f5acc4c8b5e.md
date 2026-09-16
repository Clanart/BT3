Based on my investigation, I found a valid analog in the auction bid pool's admission logic.

### Title
DoS via Sub-Wei/Minimal Bids Filling the Auction Bid Pool, Blocking Legitimate Bids - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
The `Prestaking.deposit` report describes two DoS patterns: (1) filling a bounded collection with trivially small, low-value entries to exhaust capacity, and (2) front-running that causes a legitimate transaction to be rejected once a cap is reached. The Kaia auction module's `BidPool` exhibits the first pattern: `validateBid` only requires `bid.Bid.Sign() > 0` [1](#0-0) , with no minimum bid amount, while `insertBid` enforces a hard cap `maxBidPoolSize` and rejects new bids for *new* targets once full [2](#0-1) .

### Finding Description
`validateBid` in `kaiax/auction/impl/bid_pool.go` validates block-number range, data size, gas limit, and signatures, but the only value check on the bid amount is `bid.Bid.Sign() <= 0` returning `auction.ErrZeroBid` [1](#0-0) . This means a bid of `1 wei` is fully valid and admissible, just as the `Prestaking.deposit` bug allowed 1-wei deposits.

`insertBid` only replaces an existing bid for the same `(blockNumber, targetTxHash)` pair if the new bid is strictly higher (`existingBid.Bid.Cmp(bid.Bid) >= 0` → `ErrLowBid`) [3](#0-2) . For a *new* target-tx pair (i.e., a bid the pool hasn't seen before), the only admission control is a global size cap: `if int64(len(bp.bidMap)) >= bp.maxBidPoolSize { return auction.ErrBidPoolFull }` [4](#0-3) . There is no per-bid minimum value and no value-based eviction/replacement policy across different targets — the pool is strictly FCFS-by-target once full.

Because `validateBid` additionally forbids a sender from having two different winning bids at once (`senderHasDifferentWinner` → `ErrBidSenderExists`) [5](#0-4) , an attacker must use many distinct sender addresses (unprivileged, freely generatable) to submit many 1-wei bids targeting many distinct (fabricated or real) `targetTxHash` values for the same upcoming block. Each such bid passes `validateBid` (positive bid, valid block range, valid signatures obtainable since the attacker controls both searcher and can get an auctioneer signature is required — see caveat below) and consumes one slot in `bidMap` until `maxBidPoolSize` is reached, after which `ErrBidPoolFull` is returned for all subsequent bids, including legitimate high-value bids for new targets.

### Impact Explanation
Once the pool is full of low-value junk bids, legitimate searchers cannot get their (potentially high-value) bids for *new* target transactions admitted at all — `insertBid` rejects them outright with `ErrBidPoolFull` rather than evicting the lowest bid [4](#0-3) . This blocks legitimate auction participation/settlement for the affected block, denying honest bidders and reducing MEV/auction revenue captured by the protocol — a concrete auction settlement disruption reachable by any bidder able to obtain the required auctioneer co-signature for a bid.

### Likelihood Explanation
Requires the attacker to obtain valid `auctioneerSig` for each spam bid (checked in `validateBidSigs`) [6](#0-5) ; if the auctioneer signs bids without additional gating (e.g., accepts any well-formed bid for signing), this attack is cheap because bid amount is unconstrained (1 wei) and pool admission has no minimum-value floor. If the auctioneer applies its own minimum-bid policy before signing, this significantly reduces (but the report's own reasoning about needing a code-level floor, not an off-chain policy, still applies as defense-in-depth).

### Recommendation
- Enforce a configurable minimum bid amount in `validateBid` (analogous to the report's minimum deposit recommendation), rejecting bids below a floor rather than merely `> 0`.
- When the bid pool is full (`len(bp.bidMap) >= maxBidPoolSize`), evict the lowest-value bid for a *different* target if the incoming bid is higher, rather than unconditionally returning `ErrBidPoolFull`, so genuinely higher-value bids can always displace low-value spam.

### Proof of Concept
1. Attacker generates `maxBidPoolSize` distinct sender keypairs.
2. For an upcoming block, attacker crafts `maxBidPoolSize` bids, each with `Bid = 1` (wei), distinct `TargetTxHash` values, valid `blockNumber` in range, and obtains a valid `auctioneerSig` for each (per whatever auctioneer signing policy is in place) plus valid `searcherSig`.
3. Attacker calls `AddBid` for each bid; each passes `validateBid` (`bid.Bid.Sign() > 0`) [1](#0-0)  and `insertBid` admits until `len(bp.bidMap) == maxBidPoolSize`.
4. A legitimate high-value bid for a new target arrives; `insertBid` hits `int64(len(bp.bidMap)) >= bp.maxBidPoolSize` and returns `ErrBidPoolFull` [4](#0-3) , denying the legitimate bidder for that block.

### Citations

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

**File:** kaiax/auction/impl/bid_pool.go (L356-360)
```go
	// 1. The `bid.Sender` must not be in the winner list of the same block number if the new bid isn't equal to the previous bid.
	if bp.senderHasDifferentWinner(bid) {
		bp.bidMu.RUnlock()
		return auction.ErrBidSenderExists
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L374-377)
```go
	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L397-416)
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
```
