### Title
Auction Bid Signature Verification Panics on Undersized Signature Bytes (Public Bid DoS) - ([File: kaiax/auction/bid.go])

### Summary
The `getSigner` helper in `kaiax/auction/bid.go` indexes into an attacker-supplied signature byte slice at fixed offsets (`crypto.RecoveryIDOffset`, `[0:32]`, `[32:64]`) without first validating that the slice is at least 65 bytes long. `BidData.SearcherSig` and `BidData.AuctioneerSig` are plain `[]byte` fields populated directly from externally-submitted bid data (JSON/RLP), so an unprivileged auction bidder can submit a bid whose `searcherSig` or `auctioneerSig` is shorter than 65 bytes and trigger an out-of-bounds slice access.

### Finding Description
`getSigner` is called from both `Bid.ValidateSearcherSig` and `Bid.ValidateAuctioneerSig`: [1](#0-0) 

Neither caller nor `getSigner` itself checks `len(sig)` before doing:
```go
copiedSig := slices.Clone(sig)
if copiedSig[crypto.RecoveryIDOffset] == 27 || ... // indexes byte 64
v := copiedSig[crypto.RecoveryIDOffset]
r := new(big.Int).SetBytes(copiedSig[0:32])
s := new(big.Int).SetBytes(copiedSig[32:64])
``` [2](#0-1) 

`SearcherSig` and `AuctioneerSig` are raw, unbounded `[]byte` fields on `BidData`, filled from bid submissions (JSON tag `searcherSig`/`auctioneerSig`), and `Bid.DecodeRLP` decodes `BidData` directly with no size validation on these fields either: [3](#0-2) 

This mirrors the reported bug class: a decoder/verifier reads fixed-size regions out of a variable-length, externally controlled buffer without validating the buffer is large enough, leading to an out-of-bounds access. In Go this manifests as a runtime panic (`index out of range`) rather than stack corruption, but the reachable trigger and root cause (missing length check prior to fixed-offset access on attacker data) are directly analogous.

### Impact Explanation
Because `SearcherSig`/`AuctioneerSig` come from a bid submitted by any public bidder (unprivileged, no special protocol permission required — this is explicitly one of the reachable actors listed: "auction bidder"), a single crafted bid with a signature field shorter than 65 bytes causes a panic when the node validates the bid (`ValidateSearcherSig`/`ValidateAuctioneerSig`). If bid validation is invoked in a goroutine or path lacking a `recover()`, this can crash or disrupt the node's bid-processing/auction flow, denying service to other bidders/auctioneers, or at minimum causing repeated processing failures for auction settlement — a concrete denial against the gasless/auction module's availability.

### Likelihood Explanation
High likelihood of triggering the panic: `SearcherSig`/`AuctioneerSig` are plain byte slices with no length constraints enforced at decode or field-assignment time, and the vulnerable code path executes unconditionally whenever signature validation is attempted. Any unauthenticated bidder able to submit a bid (via whatever public bid-submission RPC/API the auction module exposes) can supply a short byte array to hit this path.

### Recommendation
Add an explicit length check (`len(sig) == crypto.SignatureLength`, i.e., 65 bytes) at the top of `getSigner` (and/or when decoding `BidData`) before any indexing/slicing, returning `ErrInvalidSignature` for malformed inputs, consistent with the pattern already used elsewhere in the codebase (e.g., `crypto.Ecrecover`, `PersonalAPI.EcRecover`, `crypto/secp256k1.checkSignature`): [4](#0-3) [5](#0-4) 

### Proof of Concept
1. Construct a `BidData` (as an unprivileged bidder submitting via the auction bid-submission API) with `SearcherSig` (or `AuctioneerSig`) set to a byte slice shorter than 65 bytes, e.g. `[]byte{0x01}`.
2. Submit the bid so that it reaches `Bid.ValidateSearcherSig` / `Bid.ValidateAuctioneerSig`.
3. Execution reaches `getSigner(sig, digest)` with `len(sig) < 65`; the line `copiedSig[crypto.RecoveryIDOffset]` (offset 64) or `copiedSig[32:64]` executes an out-of-bounds slice access, panicking with `index out of range` / `slice bounds out of range`.
4. If the calling goroutine lacks a `recover()`, this crashes the node process or disrupts the auction bid-processing goroutine, denying service.

Note: I was unable to fully trace, within available tooling, whether `kaiax/auction/impl/bid_pool.go` or `kaiax/auction/impl/api.go` perform any signature-length pre-check before invoking `ValidateSearcherSig`/`ValidateAuctioneerSig`, or whether the validation call site wraps execution in a `recover()`. This should be verified to confirm the exact blast radius (goroutine crash vs. full node crash) before finalizing severity.

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

**File:** kaiax/auction/bid.go (L142-156)
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

	pub, err := crypto.SigToPub(digest, copiedSig)
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

**File:** crypto/secp256k1/secp256.go (L62-69)
```go
func checkSignature(sig []byte) error {
	if len(sig) != 65 {
		return ErrInvalidSignatureLen
	}
	if sig[64] >= 4 {
		return ErrInvalidRecoveryID
	}
	return nil
```
