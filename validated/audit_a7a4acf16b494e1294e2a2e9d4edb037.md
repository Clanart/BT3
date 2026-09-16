## Title
Out-of-bounds slice read in auction bid signature recovery due to missing length validation before parsing - ([File: kaiax/auction/impl/bid_pool.go], [File: kaiax/auction/bid.go])

### Summary
`kaiax/auction/bid.go`'s `getSigner` function parses a raw signature byte slice (`sig[crypto.RecoveryIDOffset]`, `sig[0:32]`, `sig[32:64]`) without first verifying that the slice is at least `crypto.SignatureLength` (65) bytes long. This mirrors the Contiki-NG CVE-2023-34101 bug class: a fixed-size field is read from an attacker-controlled, variable-length buffer without a preceding length check, causing an out-of-bounds read/panic when the buffer is shorter than expected.

### Finding Description
`getSigner` unconditionally clones and indexes into the signature bytes: [1](#0-0) 

`ValidateSearcherSig` and `ValidateAuctioneerSig` call `getSigner(b.SearcherSig, ...)` / `getSigner(b.AuctioneerSig, ...)` directly: [2](#0-1) 

The only call site that gates this with a length check is `BidPool.validateBidSigs`, which is used for the peer-gossip / pool insertion path: [3](#0-2) 

However, the RPC-facing `AuctionAPI` (`kaiax/auction/impl/api.go`) builds a `*auction.Bid` directly from a JSON `BidInput` (`SearcherSig`/`AuctioneerSig` are arbitrary `hexutil.Bytes` supplied by the caller) via `ToBid`: [4](#0-3) 

If the RPC submit-bid path (or any other caller) invokes `ValidateSearcherSig`/`ValidateAuctioneerSig`/`getSigner` on a `Bid` built this way before the `len(...) != crypto.SignatureLength` check that exists in `bid_pool.go`'s `validateBidSigs`, a bid with a `SearcherSig` or `AuctioneerSig` shorter than 65 bytes (e.g., empty, or 32 bytes) will cause `getSigner` to panic on out-of-range slice access (`sig[crypto.RecoveryIDOffset]` when `len(sig) < 65`, or `sig[32:64]` when `len(sig) < 64`), since Go's `[]byte` slicing panics rather than silently truncating on out-of-bounds access — this is the direct Go analog of the C out-of-bounds read from CVE-2023-34101's `dao_input_storing`.

### Impact Explanation
An out-of-bounds slice access in Go causes an immediate runtime panic, not silent memory corruption as in C. If this panic is reachable via the JSON-RPC `submitBid`/auction API without going through `BidPool.validateBidSigs`'s length guard, an unprivileged searcher/RPC caller could crash the node process (denial of service) by submitting a bid with a malformed, too-short signature field. This would affect an unprivileged auction bidder / public-RPC caller, matching an in-scope actor class.

### Likelihood Explanation
Medium-to-low confidence: I could not fully trace whether the RPC `submitBid` handler in `kaiax/auction/impl/api.go` always routes through `BidPool.validateBidSigs` (which does perform the length check) before calling `ValidateSearcherSig`/`ValidateAuctioneerSig`, or whether there is a path (e.g., a different API method, gRPC endpoint, or internal call) that invokes signature validation directly on an RPC-supplied `Bid` without the length gate. The test file `kaiax/auction/impl/api_test.go` shows `invalidSearcherSigLenBid`/`invalidAuctioneerSigLenBid` test cases that expect a clean error (`auction.ErrInvalidSearcherSig`) rather than a panic, suggesting the length check IS enforced in the actual `submitBid` flow via `validateBidSigs`. This substantially reduces confidence that this is an exploitable, unauthenticated-reachable vulnerability in the current code, and the defensive check may already fully cover the primary reachable path.

### Recommendation
Regardless of current reachability, `getSigner` should defensively validate `len(sig) == crypto.SignatureLength` at the top of the function (not only at call sites), so that any future or indirect caller cannot trigger a panic. This centralizes the fix at the root-cause function, consistent with the CVE-2023-34101 remediation of adding bounds checks before fixed-size reads.

### Proof of Concept
Given the uncertainty about whether an unguarded call path exists, a proof of concept could not be conclusively constructed from the available code index. If reachable, the PoC would be: submit a bid via the RPC bid-submission API with `searcherSig` or `auctioneerSig` set to a byte string shorter than 65 bytes (e.g., `0x1234`), bypassing/reaching a code path that calls `getSigner` before the `len(...) != crypto.SignatureLength` guard in `bid_pool.go`, causing a slice-bounds-out-of-range panic in the node.

---
**Note on confidence**: Because I could not fully verify whether all RPC/API entry points into `getSigner` are guarded by the length check found in `BidPool.validateBidSigs`, and the available test cases suggest they are, I am not fully confident this constitutes an actively exploitable Medium+ vulnerability as required by the validation rules. If a background agent or further investigation confirms an unguarded call path exists, this would qualify as a Medium (DoS/panic) finding; otherwise, the defensive fix is still recommended but the current state may already be safe.

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
