### Title
Missing length validation before slice indexing in auction bid signature recovery causes a panic reachable from unprivileged RPC bid submission - ([File: kaiax/auction/bid.go])

### Summary
`getSigner()` in `kaiax/auction/bid.go` indexes into the caller-supplied `sig` byte slice (`copiedSig[crypto.RecoveryIDOffset]`, `copiedSig[0:32]`, `copiedSig[32:64]`) without first checking `len(sig) == crypto.SignatureLength` (65 bytes). This mirrors the root cause of the reported `rustls-webpki` bug class: a parser reads fixed offsets out of a variable-length, attacker-controlled byte buffer without validating the length invariant first, leading to an index-out-of-bounds panic (denial of service) on malformed input.

### Finding Description
`getSigner` unconditionally slices/indexes the signature buffer: [1](#0-0) 

This function is called from `Bid.ValidateSearcherSig` and `Bid.ValidateAuctioneerSig`: [2](#0-1) 

The safe caller path in `BidPool.validateBidSigs` guards this correctly by checking `len(bid.SearcherSig) != crypto.SignatureLength` and `len(bid.AuctioneerSig) != crypto.SignatureLength` before calling `ValidateSearcherSig`/`ValidateAuctioneerSig`: [3](#0-2) 

However, `auction.Bid` objects can also be constructed from data that is not funneled through `validateBidSigs`. In particular, `blockchain/system/auction.DecodeAuctionCallData` builds an `auction.Bid` directly from ABI-decoded contract calldata (the `SearcherSig`/`AuctioneerSig` fields are plain `bytes` parameters with no length constraint enforced by the ABI decoder): [4](#0-3) 

If any code path calls `ValidateSearcherSig`/`ValidateAuctioneerSig` (or the underlying `getSigner`) on a `Bid` produced this way — e.g., a bid decoded from an auction entry-point contract call embedded in an arbitrary transaction, or any future/aux RPC/validation path that skips the length pre-check present in `bid_pool.go` — a `SearcherSig`/`AuctioneerSig` shorter than 65 bytes (e.g., empty or a few bytes, which the ABI `bytes` type permits with zero validation) will cause `copiedSig[crypto.RecoveryIDOffset]` or `copiedSig[0:32]`/`copiedSig[32:64]` to panic with "index out of range" or "slice bounds out of range", exactly analogous to the reported webpki bug where an unchecked BIT STRING length assumption caused `raw_bits[raw_bits.len()-1]` to panic.

Because I could not find any caller elsewhere in the given search context that invokes `ValidateSearcherSig`/`ValidateAuctioneerSig` directly on a `DecodeAuctionCallData` result without the length check, the concrete reachability of a *live* unguarded call path is not fully confirmed from the indexed code alone — this is the main uncertainty in this analog. The vulnerable pattern (`getSigner` lacking its own defensive length check) is nonetheless a real latent bug: it relies entirely on every caller externally pre-validating length, which is a fragile invariant, especially given that `Bid` objects are constructed in multiple places (`DecodeRLP`, JSON, ABI-decode) and the checked function itself provides no guarantee.

### Impact Explanation
If reachable, this is a Denial-of-Service: any unprivileged party who can get an `auction.Bid` with a malformed `SearcherSig`/`AuctioneerSig` (shorter than 65 bytes) into a code path that calls `getSigner` without first checking `len(sig) == crypto.SignatureLength` would crash the node process handling that bid (panic causes goroutine crash, which in a p2p/API handler context typically terminates the node or the RPC server, depending on recovery middleware). Because auction/bid features are part of the gasless/auction module explicitly listed as in-scope for this class of report, and because bid data ultimately originates from external bidders (searchers) and can be embedded in ordinary transaction calldata to the auction entry-point contract, this satisfies the "unprivileged... auction bidder... public-RPC caller" reachability bar in principle.

### Likelihood Explanation
Medium-Low: the only confirmed call site of `getSigner` in the current index (`bid_pool.go`) already performs the correct length check before calling into `ValidateSearcherSig`/`ValidateAuctioneerSig`, so the currently-known production path is safe. The risk is that `getSigner` itself has no internal defense-in-depth, and `DecodeAuctionCallData` in `blockchain/system/auction.go` produces `Bid` objects with fully attacker-controlled, unvalidated-length `SearcherSig`/`AuctioneerSig` fields ABI-decoded straight from calldata. If `DecodeAuctionCallData`'s result (or any other `Bid` construction path bypassing `bid_pool.validateBidSigs`) is ever passed into `ValidateSearcherSig`/`ValidateAuctioneerSig` — which is plausible given `DecodeAuctionCallData`'s stated purpose of "identifying the calldata of a bid" for auditing/monitoring/verification tooling — the panic becomes trivially triggerable by submitting a transaction or bid with a truncated signature.

### Recommendation
Add an explicit length check inside `getSigner` itself (defense in depth, independent of any caller-side check):
```go
func getSigner(sig, digest []byte) (common.Address, error) {
    if len(sig) != crypto.SignatureLength {
        return common.Address{}, ErrInvalidSignature
    }
    ...
}
```
This removes reliance on every current and future caller remembering to pre-validate signature length, closing the same class of bug reported against `rustls-webpki`'s `bit_string_flags()`.

### Proof of Concept
Conceptual reproduction (Go):
```go
package main

import (
    "fmt"
    "github.com/kaiachain/kaia/kaiax/auction"
    "math/big"
)

func main() {
    bid := &auction.Bid{
        BidData: auction.BidData{
            Sender:      /* any address */ ,
            SearcherSig: []byte{0x01, 0x02}, // malformed: far shorter than 65 bytes
        },
    }
    // panics inside getSigner: copiedSig[crypto.RecoveryIDOffset] index out of range
    _ = bid.ValidateSearcherSig(big.NewInt(1), /* verifyingContract */ , auction.AuctionVersionV2)
    fmt.Println("unreachable if panic occurs")
}
```
Any caller that constructs an `auction.Bid` with a short `SearcherSig`/`AuctioneerSig` (such as via `blockchain/system/auction.DecodeAuctionCallData` on attacker-supplied calldata) and then calls `ValidateSearcherSig`/`ValidateAuctioneerSig` without first checking `len(sig) == crypto.SignatureLength` will crash with an index-out-of-range panic, per [1](#0-0) .

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

**File:** kaiax/auction/impl/bid_pool.go (L397-419)
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

	return nil
}
```

**File:** blockchain/system/auction.go (L121-175)
```go
// DecodeAuctionCallData is the reverse function of EncodeAuctionCallData.
// It can be used to identify the calldata of a bid. The v2.1/v3.0 ABI is
// auto-detected by matching the method selector.
func DecodeAuctionCallData(encoded []byte) (*auction.Bid, error) {
	if len(encoded) <= 4 {
		return nil, errors.New("invalid encoded data")
	}

	if bytes.Equal(encoded[:4], abiV3.Methods["call"].ID) {
		decoded, err := abiV3.Methods["call"].Inputs.Unpack(encoded[4:])
		if err != nil {
			return nil, err
		}
		var bid contractsv3.IAuctionEntryPointAuctionTx
		if err := mapstructure.Decode(decoded[0], &bid); err != nil {
			return nil, err
		}
		bidData := auction.BidData{
			TargetTxHash:  bid.TargetTxHash,
			BlockNumber:   bid.BlockNumber.Uint64(),
			Sender:        bid.Sender,
			To:            bid.To,
			Nonce:         bid.Nonce.Uint64(),
			Bid:           bid.Bid,
			MaxGasPrice:   bid.MaxGasPrice,
			CallGasLimit:  bid.CallGasLimit.Uint64(),
			Data:          bid.Data,
			SearcherSig:   bid.SearcherSig,
			AuctioneerSig: bid.AuctioneerSig,
		}
		return &auction.Bid{BidData: bidData}, nil
	}

	decoded, err := abi.Methods["call"].Inputs.Unpack(encoded[4:])
	if err != nil {
		return nil, err
	}
	var bid contracts.IAuctionEntryPointAuctionTx
	if err := mapstructure.Decode(decoded[0], &bid); err != nil {
		return nil, err
	}
	bidData := auction.BidData{
		TargetTxHash:  bid.TargetTxHash,
		BlockNumber:   bid.BlockNumber.Uint64(),
		Sender:        bid.Sender,
		To:            bid.To,
		Nonce:         bid.Nonce.Uint64(),
		Bid:           bid.Bid,
		CallGasLimit:  bid.CallGasLimit.Uint64(),
		Data:          bid.Data,
		SearcherSig:   bid.SearcherSig,
		AuctioneerSig: bid.AuctioneerSig,
	}
	return &auction.Bid{BidData: bidData}, nil
}
```
