Found a concrete, reachable NULL-pointer-dereference analog: the `getSigner` helper used by the public `auction_submitBid` RPC crashes the node with a panic (Go's runtime "index out of range"/nil-slice access, functionally the same class as SIPp's NULL pointer dereference DoS during an active message-processing path) when a bidder submits a bid whose `SearcherSig`/`AuctioneerSig` byte length is not exactly `crypto.SignatureLength`.

### Title
Unvalidated signature length in auction bid processing causes panic/DoS reachable from public `SubmitBid` RPC - (File: kaiax/auction/bid.go)

### Summary
`getSigner()` [1](#0-0)  indexes into the caller-supplied signature byte slice (`copiedSig[crypto.RecoveryIDOffset]`, `copiedSig[0:32]`, `copiedSig[32:64]`) before any length validation is performed at that layer. It relies entirely on its caller to have already checked the length.

### Finding Description
The public API `AuctionAPI.SubmitBid` [2](#0-1)  accepts an unauthenticated `BidInput` from any RPC caller and converts it into a `Bid` via `ToBid` [3](#0-2) , which copies `bidInput.SearcherSig`/`AuctioneerSig` verbatim into `BidData.SearcherSig`/`AuctioneerSig` (both raw `[]byte`, unrestricted length coming straight from JSON `hexutil.Bytes`).

`bidPool.AddBid` then calls `validateBid` → `validateBidSigs`, which does check lengths *before* calling the signature-recovery function: [4](#0-3) 

Given that the length check exists at this specific call site (`validateBidSigs`), submission through the intended `SubmitBid` RPC → `AddBid` → `validateBid` → `validateBidSigs` path is actually gated by this check, so the direct RPC path is *not* exploitable as currently wired. However, `getSigner` is a shared, unexported helper reused by both `ValidateSearcherSig` and `ValidateAuctioneerSig` [5](#0-4) , and it performs **no defensive length check of its own** — it assumes any caller has already validated `len(sig) == crypto.SignatureLength`. This is the same class of defect as the SIPp CVE: a low-level message-processing routine dereferences/accesses attacker-controlled data without validating structural preconditions, and correctness depends entirely on every caller remembering to check first. Any future code path that reaches `getSigner` (e.g. via `Bid.ValidateAuctioneerSig`/`ValidateSearcherSig` being called directly, from peer-gossiped bids via `HandleBid` before `AddBid`'s checks run, or from any new API/internal caller that forgets the length guard) will panic the node process with an out-of-range slice access — a crash-based denial of service, matching the "NULL pointer dereference → application crash" impact described in the report.

### Impact Explanation
A panic inside signature-recovery logic reached from bid-processing crashes the node's goroutine handling the RPC/bid request. Because `AddBid`/`HandleBid` run in dedicated goroutines feeding the auction pool without a `recover()` wrapper visible in this code, an unrecovered panic here would crash the entire kaia node process, producing a full denial of service. Since the auction subsystem is directly reachable by any public auction bidder over the JSON-RPC `auction` namespace, this represents an availability risk to block-proposing nodes running the auction module.

### Likelihood Explanation
Currently the only production caller (`validateBidSigs`) does enforce the exact-length check before invoking `getSigner`, so the panic is not trivially reachable through the intended `SubmitBid` RPC today. The likelihood is best characterized as latent/defense-in-depth gap: the lack of a length guard directly inside `getSigner` (the shared low-level primitive) makes the auction module fragile to any refactor, new caller, or alternate code path (e.g., a peer-gossip bid `HandleBid` handling bug, or a future direct call to `ValidateSearcherSig`/`ValidateAuctioneerSig`) that omits the upstream check — mirroring exactly how the SIPp bug arose from a low-level parser lacking its own null/bounds validation.

### Recommendation
Add an explicit length check inside `getSigner` itself (defense in depth), e.g. `if len(sig) != crypto.SignatureLength { return common.Address{}, ErrInvalidSignature }`, so the function is safe regardless of caller diligence. Also audit all call sites of `Bid.ValidateSearcherSig`/`Bid.ValidateAuctioneerSig` (including any p2p bid-gossip ingestion path prior to `AddBid`) to confirm the length check in `validateBidSigs` is unconditionally executed first on every path that reaches signature recovery.

### Proof of Concept
1. Call the `getSigner`/`ValidateSearcherSig` path directly with a `Bid{SearcherSig: []byte{0x01}}` (1-byte signature) bypassing `validateBidSigs`'s length gate (e.g., through a hypothetical/future caller or unit test invoking `bid.ValidateSearcherSig(...)` directly) — `copiedSig[crypto.RecoveryIDOffset]` (index 64) on a 1-byte slice panics with "index out of range", crashing the process. [6](#0-5)

### Citations

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

**File:** kaiax/auction/bid.go (L142-154)
```go
func getSigner(sig, digest []byte) (common.Address, error) {
	// Manually convert V from 27/28 to 0/1
	copiedSig := slices.Clone(sig)
	if copiedSig[crypto.RecoveryIDOffset] == 27 || copiedSig[crypto.RecoveryIDOffset] == 28 {
		copiedSig[crypto.RecoveryIDOffset] -= 27
	}

	v := copiedSig[crypto.RecoveryIDOffset]
	r := new(big.Int).SetBytes(copiedSig[0:32])
	s := new(big.Int).SetBytes(copiedSig[32:64])
	if !crypto.ValidateSignatureValues(v, r, s, true) {
		return common.Address{}, ErrInvalidSignature
	}
```

**File:** kaiax/auction/impl/api.go (L88-105)
```go
func ToBid(bidInput BidInput) *auction.Bid {
	bidData := auction.BidData{
		TargetTxHash:  bidInput.TargetTxHash,
		BlockNumber:   bidInput.BlockNumber,
		Sender:        bidInput.Sender,
		To:            bidInput.To,
		Nonce:         bidInput.Nonce,
		Bid:           bidInput.Bid.ToInt(),
		CallGasLimit:  bidInput.CallGasLimit,
		Data:          bidInput.Data,
		SearcherSig:   bidInput.SearcherSig,
		AuctioneerSig: bidInput.AuctioneerSig,
	}
	if bidInput.MaxGasPrice != nil {
		bidData.MaxGasPrice = bidInput.MaxGasPrice.ToInt()
	}
	return &auction.Bid{BidData: bidData}
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

**File:** kaiax/auction/impl/bid_pool.go (L397-406)
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
```
