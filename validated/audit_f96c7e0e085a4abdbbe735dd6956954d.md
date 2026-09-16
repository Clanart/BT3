### Title
Out-of-bounds slice index panic in auction bid signature recovery via unvalidated signature length - ([File: kaiax/auction/bid.go])

### Summary
`getSigner` in `kaiax/auction/bid.go` indexes into a byte slice derived directly from an attacker-supplied `Bid.SearcherSig` / `Bid.AuctioneerSig` field without first validating that the slice has the expected 65-byte ECDSA signature length. This mirrors the root cause of the reported `re2` advisory: a length/format assumption about attacker-controlled input is never checked before the input is walked/indexed, producing an out-of-bounds access. In Go this manifests as an unrecovered `index out of range` panic rather than a heap read, but the reachability and impact class (attacker-controlled, unauthenticated input triggering an out-of-bounds access in a hot validation path) is directly analogous.

### Finding Description
`BidData.SearcherSig` and `BidData.AuctioneerSig` are plain `[]byte` fields populated from external input (RLP-decoded bid data or JSON from the auction/bid submission API) with no length constraint enforced at decode time: [1](#0-0) 

When a bid (or the auctioneer's counter-signature on a bid) is validated, `ValidateSearcherSig` / `ValidateAuctioneerSig` pass the raw, attacker-supplied byte slice straight into `getSigner`: [2](#0-1) 

`getSigner` immediately indexes and slices the cloned signature buffer — `copiedSig[crypto.RecoveryIDOffset]` (offset 64), `copiedSig[0:32]`, and `copiedSig[32:64]` — with **no check that `len(sig) >= 65`** beforehand: [3](#0-2) 

If a searcher or auctioneer supplies a `SearcherSig`/`AuctioneerSig` shorter than 65 bytes (including an empty slice, since `[]byte` has no default enforced length in RLP/JSON decoding), `copiedSig[crypto.RecoveryIDOffset]` or the subsequent slice expressions will panic with "index out of range" / "slice bounds out of range". Unlike the `re2` bug (a native heap OOB read causing SIGSEGV), Go's runtime converts the invalid access into a panic, but the underlying defect is the same: the code trusts an externally supplied byte buffer's length implicitly rather than validating it before indexing.

### Impact Explanation
This function sits directly on the auction-bid admission/validation path — a component explicitly reachable by an unprivileged "auction bidder" per the in-scope module list. A malformed bid (or a malformed auctioneer signature accompanying it) that reaches `ValidateSearcherSig`/`ValidateAuctioneerSig` without prior length validation will cause an unrecovered panic in whatever goroutine processes bid admission (e.g., the auction bid pool / RPC submission handler). Unless the call is wrapped in a `recover()`, this crashes the node process handling the auction module — a denial-of-service condition reachable by any unauthenticated bidder submitting a single malformed bid, consistent with a Medium-severity DoS analog to the reported CWE-125 class.

### Likelihood Explanation
High likelihood of triggering: the fields are ordinary `[]byte`/`hexutil.Bytes`-style inputs with no length assertion visible anywhere before `getSigner` is invoked, and constructing a bid with a truncated or empty `searcherSig`/`auctioneerSig` requires no special privilege — just a normal bid submission through the auction API/RLP bid gossip path.

### Recommendation
In `getSigner` (or immediately before calling it in `ValidateSearcherSig`/`ValidateAuctioneerSig`), validate `len(sig) == crypto.SignatureLength` (65 bytes) and return `ErrInvalidSignature` (or equivalent) for any other length, before performing any indexing/slicing on the buffer. This mirrors how `crypto.Ecrecover`/`sigToPub` already guard with `len(sig) != SignatureLength` checks elsewhere in the codebase (see `crypto/signature_cgo.go`/`crypto/signature_nocgo.go`), so the same guard should be applied consistently in `kaiax/auction/bid.go`.

### Proof of Concept
1. Submit (or gossip) an auction `Bid` whose `SearcherSig` (or an accompanying `AuctioneerSig`) is set to a byte slice shorter than 65 bytes, e.g. `[]byte{}` or `[]byte{0x01}`.
2. When the node calls `Bid.ValidateSearcherSig` (or `ValidateAuctioneerSig`) as part of bid admission, `getSigner` executes `copiedSig[crypto.RecoveryIDOffset]` on the undersized slice.
3. Go runtime raises `panic: runtime error: index out of range [64] with length <N>` (or a slice-bounds panic on `copiedSig[0:32]`/`copiedSig[32:64]` for slightly larger but still insufficient inputs), crashing the goroutine/process unless recovered upstream.

Note: I was not able to fully trace whether the RPC/bid-pool call sites that invoke `ValidateSearcherSig`/`ValidateAuctioneerSig` wrap this call in a `recover()` (e.g., in `kaiax/auction/impl/bid_pool.go` or `kaiax/auction/impl/api.go`); if such a recover exists, the practical impact would be limited to a rejected bid rather than a full process crash. This should be verified by inspecting those call sites directly (a full Devin session with complete file access would confirm this).

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
