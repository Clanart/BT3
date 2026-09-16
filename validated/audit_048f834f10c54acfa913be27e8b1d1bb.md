### Title
Unvalidated signature-length index access causes out-of-bounds slice panic in auction bid signature verification - (File: kaiax/auction/bid.go)

### Summary
The `getSigner` helper in `kaiax/auction/bid.go` indexes and slices a signature byte array supplied directly from an unprivileged auction bidder's `Bid` (`SearcherSig` / `AuctioneerSig`) at fixed offsets (`crypto.RecoveryIDOffset` = 64, and byte ranges `[0:32]`, `[32:64]`) before any length check is performed, mirroring the reported CVE's bug class of using an attacker-controlled index/offset to access an array without validating it against the buffer size.

### Finding Description
`BidData.SearcherSig` and `BidData.AuctioneerSig` are plain `[]byte` fields decoded directly from RLP-encoded, network/RPC-submitted bid data [1](#0-0) . When a bid is validated, `ValidateSearcherSig`/`ValidateAuctioneerSig` pass this attacker-controlled byte slice straight into `getSigner`: [2](#0-1) 

`getSigner` clones the signature and immediately accesses `copiedSig[crypto.RecoveryIDOffset]` (index 64) and slices `copiedSig[0:32]` and `copiedSig[32:64]` with **no prior check that `len(sig) >= 65`**. Only *after* these unchecked accesses does execution reach `crypto.SigToPub`, whose internal length check (`len(sig) != SignatureLength`) at that point is too late — the out-of-bounds index/slice on a short `sig` (e.g., empty, 1-byte, or 63-byte signature) panics in Go before `SigToPub` is ever called [3](#0-2) .

This is directly analogous to the OCCT VRML `coordIndex` bug: an externally supplied value (here, the signature byte-slice length/content, standing in for `coordIndex`) is used as a direct index/offset into a buffer (`copiedSig`) without validating it against the buffer's actual size, producing an out-of-bounds access.

### Impact Explanation
An auction searcher/bidder is a normal unprivileged participant who can submit `Bid` objects (via the auction bid-submission path exercised by `bid_pool.go`/`api.go`) reachable through the node's public bid RPC. By crafting a `SearcherSig` (or `AuctioneerSig`) shorter than 65 bytes, the caller triggers a runtime slice-bounds panic inside `getSigner` during signature validation. Because this runs on the node handling untrusted, single-submission input (a single crafted bid, no privileged access required), it can crash or hang the goroutine/process processing bids unless recovered further up the call stack — a denial-of-service condition consistent with the CVE's CVSS vector (`AV:L/AC:L/PR:L/UI:N/.../A:H`, availability-impact-only).

### Likelihood Explanation
High likelihood of triggering: the trigger requires only a single malformed bid submission with a short `SearcherSig`/`AuctioneerSig` byte array — no cryptographic material, no special privileges, and no complex preconditions are needed. The vulnerable code path (`getSigner`) is called on every bid signature validation.

### Recommendation
In `getSigner` (kaiax/auction/bid.go), validate `len(sig) == crypto.SignatureLength` (65 bytes) immediately after cloning `sig` and before any indexing or slicing; return `ErrInvalidSignature` (or a similar explicit error) for any other length, matching the defensive check already present later in `crypto.SigToPub`/`sigToPub`.

### Proof of Concept
1. Construct a `BidData` with `SearcherSig` (or `AuctioneerSig`) set to a byte slice shorter than 65 bytes, e.g. `[]byte{}` or `make([]byte, 10)`.
2. Submit this `Bid` through the auction bid submission path so that `ValidateSearcherSig`/`ValidateAuctioneerSig` is invoked [4](#0-3) .
3. Execution reaches `getSigner(sig, digest)`; `copiedSig[crypto.RecoveryIDOffset]` (index 64) or `copiedSig[32:64]` triggers `index out of range`/`slice bounds out of range` panic [5](#0-4)  before the length-safe check in `sigToPub` is ever reached [6](#0-5) .

Note: I was unable to fully trace whether the bid-submission RPC handler (`kaiax/auction/impl/api.go`, `bid_pool.go`) wraps this call in a `recover()`, or performs its own upstream length check on `SearcherSig`/`AuctioneerSig` before calling `ValidateSearcherSig`/`ValidateAuctioneerSig` — this could not be confirmed with the available index and should be verified in a full session before finalizing severity/impact.

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

**File:** kaiax/auction/bid.go (L142-155)
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

**File:** crypto/signature_nocgo.go (L37-57)
```go
// Ecrecover returns the uncompressed public key that created the given signature.
func Ecrecover(hash, sig []byte) ([]byte, error) {
	pub, err := sigToPub(hash, sig)
	if err != nil {
		return nil, err
	}
	bytes := pub.SerializeUncompressed()
	return bytes, err
}

func sigToPub(hash, sig []byte) (*secp256k1.PublicKey, error) {
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
```
