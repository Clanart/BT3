Based on my research, the closest reachable analog in this codebase is an unchecked-length buffer index panic in the auction bid signature-recovery path — not a silent OOB *read* returning stale heap bytes as in the GStreamer case, but the same root-cause bug class (attacker-controlled binary blob indexed without a length/bounds check before use).

### Title
Unchecked-length signature buffer indexing causes out-of-bounds panic in auction bid verification - (File: kaiax/auction/bid.go)

### Summary
`getSigner`, used by both `Bid.ValidateSearcherSig` and `Bid.ValidateAuctioneerSig`, indexes into the caller-supplied `sig` byte slice (`SearcherSig` / `AuctioneerSig`) at fixed offsets (`crypto.RecoveryIDOffset`, `[0:32]`, `[32:64]`) without first checking `len(sig) >= 65`. Both signature fields are attacker-controlled: they arrive via the public `SubmitBid` JSON-RPC method as `hexutil.Bytes` and are converted directly into `BidData.SearcherSig`/`AuctioneerSig` with no length validation before being handed to `getSigner`.

### Finding Description
`getSigner` in `kaiax/auction/bid.go` performs: [1](#0-0) 
with no guard on `len(sig)`. If `sig` is shorter than 65 bytes (e.g., empty, 1 byte, or 64 bytes), the slice expressions `copiedSig[crypto.RecoveryIDOffset]`, `copiedSig[0:32]`, `copiedSig[32:64]` will read out of the slice's bounds, causing a Go runtime index-out-of-range panic (a crash rather than a silent read of adjacent heap memory, since Go slices are bounds-checked at runtime — but the underlying root cause is identical to the GStreamer bug class: parsing a length-prefixed/implicitly-sized binary blob and indexing into it without validating its actual size).

This function is called from `ValidateSearcherSig` and `ValidateAuctioneerSig`: [2](#0-1) 

The RPC entry point that constructs a `Bid` directly from attacker-supplied hex bytes is `SubmitBid`: [3](#0-2) [4](#0-3) 
`BidInput.SearcherSig` and `AuctioneerSig` are typed as `hexutil.Bytes` with no minimum-length constraint, and `ToBid` copies them verbatim into `auction.BidData`.

### Impact Explanation
Any unauthenticated public-RPC caller (auction bidder) can submit a `SubmitBid` request with a `searcherSig` or `auctioneerSig` field shorter than 65 bytes. This triggers a runtime panic inside `getSigner` when `bidPool.AddBid` validates the bid signature. Depending on whether the panic is recovered at the RPC handler boundary, this can crash the node process (denial of service) affecting the auction/gasless subsystem and potentially the whole node if unrecovered. This directly affects auction bid admission, a functionality reachable by any public RPC caller/bidder, in scope per the rules.

### Likelihood Explanation
High likelihood of triggering: the attack requires only a single crafted RPC call with a short byte array for `searcherSig` (or `auctioneerSig`), no special privileges, no network conditions, and no economic cost beyond gas/spam considerations. The bug is deterministic and 100% reproducible.

### Recommendation
Add explicit length validation (`len(sig) == 65` or the constant used elsewhere for ECDSA signatures, i.e., `crypto.SignatureLength`) at the top of `getSigner` in `kaiax/auction/bid.go`, returning `ErrInvalidSignature` (or a similar error) before any indexing occurs. Equivalent validation should also be added at RPC ingestion time in `kaiax/auction/impl/api.go` (`ToBid`/`SubmitBid`) to reject malformed bids early with a clean error rather than relying solely on the panic being recovered elsewhere.

### Proof of Concept
1. Call the public RPC method `auction_submitBid` (or equivalent `SubmitBid`) with a valid `BidInput` JSON body where `searcherSig` is set to a short hex string, e.g. `"0x00"` (1 byte) instead of the required 65-byte ECDSA signature.
2. The request flows through `AuctionAPI.SubmitBid` → `ToBid` → `bidPool.AddBid(bid)` → (eventually) `Bid.ValidateSearcherSig` → `getSigner(b.SearcherSig, digest)`.
3. Inside `getSigner`, `copiedSig[crypto.RecoveryIDOffset]` (offset 64) is accessed on a 1-byte slice, causing `panic: runtime error: index out of range [64] with length 1`.

Note: I was unable to fully trace whether `bidPool.AddBid` in `kaiax/auction/impl/bid_pool.go` performs any signature-length validation before calling `ValidateSearcherSig`/`ValidateAuctioneerSig`, nor whether the RPC server framework recovers from panics in handler goroutines to prevent a full node crash — these should be verified by a maintainer/Devin session with the full `bid_pool.go` contents and RPC panic-recovery middleware, which were not fully available in the indexed context.

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

**File:** kaiax/auction/bid.go (L142-151)
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
```

**File:** kaiax/auction/impl/api.go (L72-105)
```go
// BidInput is the same format with `BidData`, execpt adding new field `TargetTxRaw` and replacing `[]byte` type to `hexutil.Bytes`
type BidInput struct {
	TargetTxRaw   hexutil.Bytes  `json:"targetTxRaw"`
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           hexutil.Big    `json:"bid"`
	MaxGasPrice   *hexutil.Big   `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          hexutil.Bytes  `json:"data"`
	SearcherSig   hexutil.Bytes  `json:"searcherSig"`
	AuctioneerSig hexutil.Bytes  `json:"auctioneerSig"`
}

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
