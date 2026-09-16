## Title
Auction bid signature verification lacks length validation before fixed-offset byte-slice access, causing a panic on attacker-controlled `SearcherSig`/`AuctioneerSig` - (File: kaiax/auction/bid.go)

## Summary
The xrdp CVE describes a fixed-size buffer used to forward externally supplied data without validating its length before copying/using it, leading to memory corruption. The Kaia analog is `getSigner` in the auction bid module, which indexes and slices an externally supplied signature byte slice at fixed offsets (`0`, `32`, `64`) without first checking `len(sig)`, so a malformed `Bid.SearcherSig`/`Bid.AuctioneerSig` shorter than 65 bytes causes an out-of-bounds slice access panic instead of returning a graceful error.

## Finding Description
`BidData.SearcherSig` and `BidData.AuctioneerSig` are plain `[]byte` fields with no length constraint enforced at decode time [1](#0-0) . They are RLP-decoded directly from the wire/RPC input via `Bid.DecodeRLP`, which just decodes into `BidData` without any signature length check [2](#0-1) .

Both `ValidateSearcherSig` and `ValidateAuctioneerSig` pass these attacker-controlled byte slices straight into `getSigner` [3](#0-2) .

`getSigner` clones the signature and immediately indexes/slices it at fixed offsets without validating its length first:
```go
func getSigner(sig, digest []byte) (common.Address, error) {
	copiedSig := slices.Clone(sig)
	if copiedSig[crypto.RecoveryIDOffset] == 27 || copiedSig[crypto.RecoveryIDOffset] == 28 {
		copiedSig[crypto.RecoveryIDOffset] -= 27
	}
	v := copiedSig[crypto.RecoveryIDOffset]
	r := new(big.Int).SetBytes(copiedSig[0:32])
	s := new(big.Int).SetBytes(copiedSig[32:64])
	...
``` [4](#0-3) 

`crypto.RecoveryIDOffset` is `64` (per the ECDSA signature layout used throughout the codebase, matching `crypto.SignatureLength = 65`). If `sig` (i.e., an attacker-supplied `SearcherSig` or `AuctioneerSig`) has length less than 65 bytes — including the empty-slice/zero-value case — the first indexing `copiedSig[crypto.RecoveryIDOffset]` panics with an index-out-of-range runtime error. This is structurally identical to the xrdp bug class: externally supplied, length-unchecked data is consumed by fixed-offset buffer operations.

This bid data is reachable through:
- the public `auction_submitBid` RPC API (per the module's documented interface) [5](#0-4) ,
- the P2P `BidMsg` handler which decodes and forwards a `Bid` to the auction module (the message-level size gate only checks total message size, not internal field lengths) [6](#0-5) .

Neither path is shown to check `len(SearcherSig) == 65` / `len(AuctioneerSig) == 65` before signature validation is invoked, based on the code paths retrieved (`bid.go`, `bid_pool.go` init/removal logic). The bid pool README enumerates validation rules for size, gas limit, and "SearcherSig and AuctioneerSig must be valid" [7](#0-6) , but the length check is not performed prior to the fixed-offset access inside `getSigner` itself, meaning any caller of `ValidateSearcherSig`/`ValidateAuctioneerSig` that does not itself pre-validate length is exposed. I was not able to fully retrieve the exact `AddBid`/validation call site within `bid_pool.go` in this session (index size limits truncated the relevant portion of the file), so it's not confirmed whether an upstream length check exists immediately before these calls; this should be verified directly in a full checkout.

## Impact Explanation
A goroutine panic from an out-of-range slice index in Go, if unrecovered, crashes the process. Since bid handling runs in the bid-message processing loop / RPC handler goroutine of a Consensus Node, a single malformed bid (via `auction_submitBid` or `BidMsg`) could crash or destabilize a CN, which is a denial-of-service impacting auction settlement (KIP-249) availability. This satisfies the "state divergence between honest nodes" / DoS criteria at High-severity level given it is remotely triggerable pre-consensus by any auction bidder or peer sending a bid.

## Likelihood Explanation
Likelihood is high if no length pre-check exists on the call path, since constructing a `Bid` with a short `SearcherSig`/`AuctioneerSig` (e.g., 0 or 1 byte) requires no special privilege — it can be submitted by any auction bidder via the documented `auction_submitBid` RPC or relayed via `BidMsg`.

## Recommendation
In `getSigner` (kaiax/auction/bid.go), validate `len(sig) == crypto.SignatureLength` (65) before any indexing/slicing, returning `ErrInvalidSignature` otherwise — mirroring the length checks already present in `crypto.sigToPub`/`VerifySignature` [8](#0-7) . Additionally, enforce the same length check as early as possible in bid ingestion (RPC and `BidMsg` handling) before any cryptographic processing.

## Proof of Concept
1. Construct a `Bid` (`BidData`) with `SearcherSig = []byte{}` (or any slice shorter than 65 bytes) and a valid `Sender`, `TargetTxHash`, etc.
2. Submit it via `auction_submitBid` RPC, or send it as a `BidMsg` from a peer connection.
3. The bid pool eventually calls `Bid.ValidateSearcherSig`, which calls `getSigner(b.SearcherSig, digest)`.
4. `copiedSig[crypto.RecoveryIDOffset]` (index 64) is accessed on a slice shorter than 65 bytes, causing `index out of range` panic and crashing the goroutine/node if unrecovered.

### Citations

**File:** kaiax/auction/bid.go (L32-44)
```go
type BidData struct {
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           *big.Int       `json:"bid"`
	MaxGasPrice   *big.Int       `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          []byte         `json:"data"`
	SearcherSig   []byte         `json:"searcherSig"`
	AuctioneerSig []byte         `json:"auctioneerSig"`
}
```

**File:** kaiax/auction/bid.go (L61-68)
```go
func (b *Bid) DecodeRLP(s *rlp.Stream) error {
	var dec BidData
	if err := s.Decode(&dec); err != nil {
		return err
	}
	b.BidData = dec
	return nil
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

**File:** kaiax/auction/README.md (L13-24)
```markdown
## Bid pool validation rules

A bid pool is responsible for managing the valid bids from the `Auctioneer`. The bid must satisfy the following rules:

1. The `bid.Sender` must not be in the winner list of the same block number if the new bid doesn't have the same target block and hash as the previous bid.
2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
3. The `bid.Bid` must be greater than 0.
4. The `bid.Data` size must be less than or equal to `BidTxMaxDataSize`.
5. The `bid.CallGasLimit` must be less or equal to `BidTxMaxCallGasLimit`.
6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.

Please note that `Auctioneer` also validates the searcher's bid according to the KIP-249.
```

**File:** kaiax/auction/README.md (L58-60)
```markdown
## APIs

### auction_submitBid
```

**File:** node/cn/handler_msg_test.go (L878-909)
```go
	// The size gate runs before msg.Decode, so an oversized bid never reaches the pool.
	for _, tc := range []struct {
		name     string
		dataLen  int
		wantCall bool
	}{
		{"max legal data reaches the pool", int(auction_impl.BidTxMaxDataSize), true},
		{"oversized data is refused", int(maxBidMsgSize) + 1, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctrl := gomock.NewController(t)
			defer ctrl.Finish()

			module := auction_mock.NewMockAuctionModule(ctrl)
			peer := NewMockPeer(ctrl)
			peer.EXPECT().ConnType().Return(common.CONSENSUSNODE).AnyTimes()
			if tc.wantCall {
				peer.EXPECT().GetID().Return(nodeids[0].String()).Times(1)
				module.EXPECT().HandleBid(gomock.Any(), gomock.Any()).Times(1)
			}

			sized := &auction.Bid{BidData: bidData}
			sized.Data = make([]byte, tc.dataLen)
			err := handleBidMsg(&ProtocolManager{auctionModule: module}, peer, generateMsg(t, BidMsg, sized))

			if tc.wantCall {
				assert.NoError(t, err)
			} else {
				assert.ErrorContains(t, err, "Message too long")
			}
		})
	}
```

**File:** crypto/signature_nocgo.go (L47-53)
```go
func sigToPub(hash, sig []byte) (*secp256k1.PublicKey, error) {
	if len(sig) != SignatureLength {
		return nil, errors.New("invalid signature")
	}
	if len(hash) != DigestLength {
		return nil, fmt.Errorf("hash is required to be exactly %d bytes (%d)", DigestLength, len(hash))
	}
```
