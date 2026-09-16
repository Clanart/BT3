## Analog Vulnerability Found

### Title
Unbounded slice indexing on attacker-supplied auction bid signatures causes RPC-triggered panic (DoS) - ([File: kaiax/auction/bid.go])

### Summary
The NuttX CVE-2025-35003 stems from Bluetooth HCI/UART code that reads attacker-controlled byte buffers and performs fixed-offset memory accesses without validating the buffer length first, leading to out-of-bounds writes/reads and crashes. The Kaia codebase has a structurally identical bug class in its auction module: `getSigner()` indexes into a caller-supplied signature byte slice at fixed offsets (`RecoveryIDOffset`, `[0:32]`, `[32:64]`) without first checking that the slice is the required 65-byte length.

### Finding Description
`getSigner` in [1](#0-0)  clones the caller-supplied `sig` byte slice and immediately indexes it at `crypto.RecoveryIDOffset` (65th byte, index 64) and slices it at `[0:32]` and `[32:64]`, with no length validation performed beforehand: [2](#0-1) 

This function is called from `ValidateSearcherSig` and `ValidateAuctioneerSig` with `b.SearcherSig` / `b.AuctioneerSig`: [3](#0-2) 

These fields (`SearcherSig`, `AuctioneerSig`) originate directly from the public `auction_submitBid` RPC input `BidInput`, which is decoded as raw `hexutil.Bytes` with no length constraint and converted to a `Bid` via `ToBid`: [4](#0-3) [5](#0-4) 

An auction bidder can submit a `BidInput` where `searcherSig` or `auctioneerSig` is shorter than 65 bytes (e.g., empty, or a few bytes). Since Go slice indexing/slicing beyond bounds panics rather than returning an error, `copiedSig[crypto.RecoveryIDOffset]` or `copiedSig[32:64]` will panic with "index out of range" / "slice bounds out of range" when `len(sig) < 65`.

### Impact Explanation
This is directly analogous to the reported bug class: parsing an attacker-supplied, variable-length byte buffer with fixed-offset accesses and no length guard, causing memory-safety violations. In Go, the manifestation is a runtime panic instead of memory corruption, but the practical impact is the same class of denial-of-service: a single unprivileged RPC caller (auction bidder) submitting a malformed bid can crash the goroutine handling `AddBid`/`SubmitBid`, and if this happens on a goroutine without panic recovery in the bid-processing path, it can crash the entire node process, disrupting block production/validation availability for that node.

### Likelihood Explanation
High. The `auction_submitBid` RPC endpoint is reachable by any external bidder without special privileges (though the API is marked `Public: false`, indicating it is namespace-restricted rather than authenticated — reachable by any client permitted to call the auction namespace). No signature format validation (e.g., length == 65) occurs before the vulnerable indexing in `getSigner`. Triggering the panic requires only a single crafted bid submission with a truncated `searcherSig` or `auctioneerSig`.

### Recommendation
Add an explicit length check at the start of `getSigner` (and/or in `ValidateSearcherSig`/`ValidateAuctioneerSig`/`ToBid`) rejecting any `sig` whose length is not exactly `crypto.SignatureLength` (65 bytes), returning `ErrInvalidSignature` before any indexing occurs, mirroring the length checks already present elsewhere in the codebase (e.g., `crypto.Ecrecover`, `PersonalAPI.EcRecover`, `KaiaTransactionAPI.RecoverFromMessage`): [6](#0-5) [7](#0-6) 

### Proof of Concept
1. Call the `auction_submitBid` RPC method with a `BidInput` where `searcherSig` (or `auctioneerSig`) is set to `"0x"` (empty) or a short byte string (e.g., 1–64 bytes), and other required fields populated with valid-looking values (or values that pass earlier `toTx`/hash checks).
2. `SubmitBid` → `ToBid` → `bidPool.AddBid` will eventually invoke `ValidateSearcherSig`/`ValidateAuctioneerSig` → `getSigner(b.SearcherSig, digest)`.
3. Since `len(sig) < 65`, the line `copiedSig[crypto.RecoveryIDOffset]` (or the `[32:64]` slice) panics with "index out of range" / "slice bounds out of range [:64] with capacity N", crashing the handling goroutine.

Note: I was unable to fully trace `bidPool.AddBid` in `kaiax/auction/impl/bid_pool.go` to confirm the exact call ordering (e.g., whether any prior validation short-circuits before `getSigner` is reached in all code paths) due to running out of tool iterations; this should be verified by reading `kaiax/auction/impl/bid_pool.go` and its tests (`bid_pool_test.go`) before treating this as fully confirmed in production configuration.

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

**File:** crypto/signature_cgo.go (L36-49)
```go
// Ecrecover returns the uncompressed public key that created the given signature.
func Ecrecover(hash, sig []byte) ([]byte, error) {
	if len(sig) != SignatureLength {
		return nil, errors.New("invalid signature")
	}
	if len(hash) != DigestLength {
		return nil, fmt.Errorf("hash is required to be exactly %d bytes (%d)", DigestLength, len(hash))
	}
	// Enforce canonical Ethereum recovery id v ∈ {0, 1}.
	if sig[RecoveryIDOffset] >= 2 {
		return nil, errors.New("invalid signature recovery id")
	}
	return secp256k1.RecoverPubkey(hash, sig)
}
```

**File:** api/api_personal.go (L488-494)
```go
func (s *PersonalAPI) EcRecover(ctx context.Context, data, sig hexutil.Bytes) (common.Address, error) {
	if len(sig) != crypto.SignatureLength {
		return common.Address{}, errors.New("signature must be 65 bytes long")
	}
	if sig[crypto.RecoveryIDOffset] != 27 && sig[crypto.RecoveryIDOffset] != 28 {
		return common.Address{}, errors.New("invalid signature (V is not 27 or 28)")
	}
```
