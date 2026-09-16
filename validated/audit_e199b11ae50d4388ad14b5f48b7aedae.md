## Analysis

The reachable analog exists in the **Kaia auction module**, which has two entry points that funnel bids into the identical expensive `BidPool.AddBid` path, but only one of the two is rate-limited — the same "fix applied to one entry, not to its sibling" pattern as CVE-2026-54037.

### Title
Missing rate limiting on `auction_submitBid` RPC allows unrate-limited resource exhaustion, unlike the P2P bid-gossip path - (File: kaiax/auction/impl/api.go)

### Summary
`kaiax/auction/impl/bid_pool.go`'s `HandleBid` (invoked for bids arriving from p2p peers) enforces a per-peer token-bucket rate limiter (`checkRateLimit`, capped at `bidsPerSecondPerPeer = 300`) before a bid is queued for processing. However, the sibling entry point `AuctionAPI.SubmitBid` (the public JSON-RPC `auction_submitBid` method) calls `bidPool.AddBid` directly with **no rate limiting whatsoever**, even though it performs the exact same (and additional) expensive work.

### Finding Description
`AuctionAPI.SubmitBid` at [1](#0-0)  only increments a metrics counter (`numBidRequestCounter.Inc(1)`) — not an actual limiter — before: (1) RLP-decoding the target transaction and submitting it to the tx pool via `api.a.Backend.SendTx`, and (2) calling `bidPool.AddBid(bid)`, which runs `validateBid` → `validateBidSigs`, performing **two ECDSA signature recoveries** (`ValidateSearcherSig` and `ValidateAuctioneerSig`) at [2](#0-1)  plus map lookups under `bidMu`.

By contrast, the P2P-delivered path `BidPool.HandleBid` explicitly rate-limits each peer before this same expensive work is scheduled: [3](#0-2) , using a `rate.Limiter` per peer ID at [4](#0-3) .

This is structurally identical to the CVE-2026-54037 bug class: the same expensive operation (`AddBid`/signature verification/DB writes) is reachable from two entry points, and a rate-limit fix was applied to only one of them (the peer/network path) while the other (the RPC path, reachable by any auction bidder / public-RPC caller) was left unprotected.

### Impact Explanation
A single RPC caller (auction bidder or any client with access to the `auction` namespace) can call `auction_submitBid` in a tight loop, each call forcing two secp256k1 signature recoveries, an RLP transaction decode, a call into `Backend.SendTx` (touching the tx pool), and a locked bid-pool insertion attempt — with zero throttling. Compare this to a peer on the p2p network attempting the same, who is capped at 300 bids/sec by `checkRateLimit`. This asymmetry lets an authorized RPC caller trivially exhaust CPU (signature verification cost) and lock contention on `bidMu`/`peerRateLimiterMu`, degrading the auction module (and by extension block-building/auction settlement) for all other legitimate bidders — a resource-exhaustion DoS matching the medium-severity CVSS profile of the reference advisory (AV:N/AC:L/PR:L/UI:N .../A:H).

### Likelihood Explanation
High likelihood: `auction_submitBid` is a normal, intended entry point for any auction bidder — no special privilege beyond RPC access is required, unlike the p2p path, which requires participating as a connected peer. Note that the `auction` RPC namespace is registered with `Public: false` at [5](#0-4) , so exposure depends on node RPC-API configuration; wherever it is enabled for bidders (as intended for the auction/MEV workflow), this gap is directly exploitable with a trivial request loop.

### Recommendation
Add the same per-caller/per-sender rate limiting used in `HandleBid` (or an equivalent, e.g., keyed by `bid.Sender` or by RPC-connection identity) to `AuctionAPI.SubmitBid` before calling `bidPool.AddBid`, so that RPC-submitted bids are subject to the same throttling as p2p-gossiped bids.

### Proof of Concept
1. Enable the `auction` RPC namespace on a target node.
2. Craft a `BidInput` payload with a valid-looking `targetTxRaw`, `searcherSig`, and `auctioneerSig` (they need not be valid — the point is to force signature-recovery + validation compute, and invalid ones are cheap to churn through the same `ecrecover` path).
3. Issue repeated `auction_submitBid` JSON-RPC calls (e.g., thousands per second from a single client) via `curl`/RPC client, far exceeding the 300/sec cap enforced on the p2p path.
4. Observe that no throttling or backoff occurs at the RPC layer (`numBidRequestCounter` only increments a metric), and node CPU/lock contention rises linearly with request rate, unlike the bounded impact `HandleBid` guarantees for p2p peers.

### Citations

**File:** kaiax/auction/impl/api.go (L48-57)
```go
func (a *AuctionModule) APIs() []rpc.API {
	return []rpc.API{
		{
			Namespace: "auction",
			Version:   "1.0",
			Service:   newAuctionAPI(a),
			Public:    false,
		},
	}
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

**File:** kaiax/auction/bid.go (L94-130)
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

**File:** kaiax/auction/impl/bid_pool.go (L439-455)
```go
// checkRateLimit checks if the peer is within rate limit
func (bp *BidPool) checkRateLimit(peerID string) bool {
	bp.peerRateLimiterMu.Lock()
	defer bp.peerRateLimiterMu.Unlock()

	limiter, exists := bp.peerRateLimiter.Get(peerID)
	if !exists {
		// Create new rate limiter for this peer
		// Use burst equal to the rate limit (we only use rate limit, not the burst)
		limiter = rate.NewLimiter(rate.Limit(bidsPerSecondPerPeer), bidsPerSecondPerPeer)
		bp.peerRateLimiter.Add(peerID, limiter)
	}

	// It'll simply discard the bid if the rate limit is exceeded
	// We don't need to reserve for a bid here because the original bid will be sent from auctioneer through different channel (see #api.SubmitBid)
	return limiter.(*rate.Limiter).Allow()
}
```
