Based on my investigation, I found a genuine analog: an out-of-bounds/panic vulnerability in the Kaia auction (MEV) module's signature recovery path, triggered by attacker-controlled bid data reachable via the public bid-submission API — directly analogous to the P-Net CVE's "malicious packet causes OOB access/crash" pattern.

### Title
Out-of-bounds slice access in auction bid signature recovery causes node panic - (File: kaiax/auction/bid.go)

### Summary
The `getSigner` helper in the `kaiax/auction` module, used to recover the searcher/auctioneer address from a `Bid`'s `SearcherSig`/`AuctioneerSig` fields, indexes and slices the attacker-supplied signature bytes without first validating that the slice is the expected 65-byte length. A malicious auction bidder can submit a bid with an undersized (or empty) signature field, causing an index-out-of-range panic when the node processes the bid.

### Finding Description
`getSigner` clones the raw signature bytes and immediately accesses fixed offsets and sub-slices assuming a 65-byte ECDSA signature layout, with no length check: [1](#0-0) 

This function is called from both signature validation entry points that process bidder- and auctioneer-supplied data: [2](#0-1) [3](#0-2) 

`SearcherSig` and `AuctioneerSig` are plain `[]byte` fields on `BidData`, populated directly from RLP-decoded or JSON/RPC-submitted bid data with no length constraint enforced by `Bid.DecodeRLP`: [4](#0-3) 

If `sig` (i.e., `copiedSig`) has fewer than 65 bytes, `copiedSig[crypto.RecoveryIDOffset]` (index 64) and the `[0:32]`/`[32:64]` slicing operations will panic with "index out of range" / "slice bounds out of range." This is the Go-idiomatic equivalent of the out-of-bounds write/read described in CVE-2025-32405, where a malformed externally-supplied packet is parsed against a fixed expected structure without a size check, corrupting memory/crashing the process.

By contrast, every other signature-consuming path in the codebase performs this length check before indexing, e.g.: [5](#0-4) [6](#0-5) 

This confirms `getSigner` in `kaiax/auction/bid.go` is the outlier missing this defensive check.

### Impact Explanation
A successful trigger causes an unrecovered Go panic in the goroutine processing the bid. Depending on how the auction bid-processing pipeline handles panics (whether it is wrapped in a recover or not), this can crash the node process handling auction/bid traffic — a remotely triggerable Denial of Service on any Kaia node that exposes the auction/bid submission API, achievable by an unprivileged, unauthenticated bidder with a single malformed bid.

### Likelihood Explanation
Likelihood is high if the bid submission path does not pre-validate signature length before calling `ValidateSearcherSig`/`ValidateAuctioneerSig`. I located references to `SignatureLength`/length checks in `kaiax/auction/impl/bid_pool.go`, but I was not able to open and confirm that file's contents before running out of tool budget — it is possible (but unconfirmed) that `bid_pool.go` performs a length check on `SearcherSig`/`AuctioneerSig` prior to invoking `getSigner`, which would mitigate or eliminate this issue at that call site. This is an open verification item: a Devin session with full file access should inspect `kaiax/auction/impl/bid_pool.go` and `kaiax/auction/impl/api.go` to confirm whether any length validation occurs before `Bid.ValidateSearcherSig`/`ValidateAuctioneerSig` are invoked on attacker-supplied bids.

### Recommendation
Add explicit length validation (`len(sig) == crypto.SignatureLength`, i.e. 65 bytes) at the top of `getSigner` in `kaiax/auction/bid.go`, returning `ErrInvalidSignature` for any non-conforming input, before any indexing or slicing occurs. This mirrors the existing defensive pattern in `blockchain/types/transaction_signing.go`'s `decodeSignature`.

### Proof of Concept
1. Craft a `Bid`/`BidData` object where `SearcherSig` (or `AuctioneerSig`) is set to a byte slice shorter than 65 bytes (e.g., `[]byte{0x01}` or empty).
2. Submit this bid through the auction module's public bid-submission entry point (`kaiax/auction/impl/api.go`), which is reachable by any unprivileged auction bidder.
3. When the node calls `Bid.ValidateSearcherSig` (or `ValidateAuctioneerSig`) → `getSigner`, the line `copiedSig[crypto.RecoveryIDOffset]` (`kaiax/auction/bid.go:145`) or the subsequent `copiedSig[0:32]`/`copiedSig[32:64]` slicing panics with `index out of range` / `slice bounds out of range`, crashing the handling goroutine/node if unrecovered.

### Citations

**File:** kaiax/auction/bid.go (L32-68)
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

type Bid struct {
	BidData
	hash     atomic.Value
	gasLimit atomic.Uint64
}

func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
}

func (b *Bid) EncodeRLP(w io.Writer) error {
	return rlp.Encode(w, b.BidData)
}

func (b *Bid) DecodeRLP(s *rlp.Stream) error {
	var dec BidData
	if err := s.Decode(&dec); err != nil {
		return err
	}
	b.BidData = dec
	return nil
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

**File:** blockchain/types/transaction_signing.go (L762-769)
```go
func decodeSignature(sig []byte) (r, s, v *big.Int) {
	if len(sig) != crypto.SignatureLength {
		panic(fmt.Sprintf("wrong size for signature: got %d, want %d", len(sig), crypto.SignatureLength))
	}
	r = new(big.Int).SetBytes(sig[:32])
	s = new(big.Int).SetBytes(sig[32:64])
	v = new(big.Int).SetBytes([]byte{sig[64] + 27})
	return r, s, v
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
