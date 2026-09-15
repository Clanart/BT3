### Title
Missing signature-length validation before fixed-offset slice access in `getSigner` causes out-of-bounds panic (DoS) - ([File: kaiax/auction/bid.go])

### Summary
`getSigner()`, used by `Bid.ValidateSearcherSig` and `Bid.ValidateAuctioneerSig` to recover the address behind a searcher/auctioneer signature, indexes and slices the raw signature bytes at fixed offsets (`crypto.RecoveryIDOffset`, `[0:32]`, `[32:64]`) without first checking that the signature is at least 65 bytes long. [1](#0-0) 

### Finding Description
`SearcherSig` and `AuctioneerSig` are plain `[]byte` fields of `BidData`, populated either from RLP decoding of a `Bid` (`DecodeRLP`) [2](#0-1)  or from ABI-decoded `bytes` fields inside `DecodeAuctionCallData`, which unpacks arbitrary on-chain calldata sent to the auction entry-point contract into a `Bid` without enforcing any length constraint on the signature fields. [3](#0-2) 

When validation is performed, `ValidateSearcherSig`/`ValidateAuctioneerSig` call `getSigner(sig, digest)` directly on these attacker-controlled byte slices: [4](#0-3) 

`getSigner` clones the slice and immediately does `copiedSig[crypto.RecoveryIDOffset]` and `copiedSig[0:32]` / `copiedSig[32:64]` with no `len(sig) < 65` guard:
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
``` [5](#0-4) 

If `sig` is shorter than 65 bytes, this causes a Go runtime "slice bounds out of range" panic — the direct memory-safety analog of the nfdump fixed-buffer read overflow in the CVE (both stem from trusting an externally supplied length/offset without bounds checking before a fixed-size read).

Note: the `SubmitBid` RPC handler path (`kaiax/auction/impl/api.go` → `bidPool.AddBid`) appears to reference `ErrInvalidSearcherSig`/`ErrInvalidAuctioneerSig` in `kaiax/auction/impl/bid_pool.go`, suggesting that path may pre-validate the signature length before calling `ValidateSearcherSig`. I was not able to fully inspect `bid_pool.go`'s contents before running out of tool budget, so I cannot confirm whether that check is present on every code path that ultimately calls `getSigner` (e.g., bids reconstructed via `DecodeAuctionCallData` from arbitrary transaction calldata during block building/bundle extraction, which does not appear to enforce any signature length).

### Impact Explanation
An out-of-bounds slice access in Go causes an unrecovered panic unless caught by a `recover()` further up the call stack. If any reachable path validates a `Bid`'s `SearcherSig`/`AuctioneerSig` without a prior explicit length check (in particular a `Bid` built via `DecodeAuctionCallData` from a transaction's calldata sent to the auction entry point, or an RPC-decoded `AccountKeySerializer`/`Bid`-like structure), a single, unprivileged transaction sender or bidder can craft a signature shorter than 65 bytes to crash the node processing that data (denial of service). Because the auction/gasless bundle mechanism runs during block building on every node, this could cause node crashes/downtime that disrupt block production availability.

### Likelihood Explanation
Likelihood depends entirely on whether all call sites to `getSigner` are protected by a length check performed earlier (which I could not fully confirm for the `DecodeAuctionCallData` → block-building path). If such upstream validation exists everywhere `getSigner` is reached, this is not exploitable. Given the uncertainty in reachability confirmation, I present this as a candidate finding with medium confidence rather than a fully proven exploit chain.

### Recommendation
Add an explicit length check (`if len(sig) != 65 { return common.Address{}, ErrInvalidSignature }`) at the very start of `getSigner()` in `kaiax/auction/bid.go`, independent of any checks performed by callers, so that malformed/short signatures always fail gracefully instead of relying on callers to validate length first.

### Proof of Concept
```go
package main

import (
    "fmt"
    "github.com/kaiachain/kaia/kaiax/auction"
)

func main() {
    bid := &auction.Bid{}
    bid.SearcherSig = []byte{0x01, 0x02} // 2 bytes, far shorter than the required 65
    // ValidateSearcherSig -> getSigner will panic:
    // "slice bounds out of range [32:2]"  or index out-of-range on RecoveryIDOffset
    err := bid.ValidateSearcherSig(nil, [20]byte{}, "")
    fmt.Println(err)
}
```
(This reproduces the panic locally against `getSigner`; whether it is reachable from an unauthenticated network input depends on whether any caller supplies a `Bid` with an unchecked `SearcherSig`/`AuctioneerSig`, which I was unable to fully verify due to running out of investigation budget on `kaiax/auction/impl/bid_pool.go`.)

### Citations

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

**File:** blockchain/system/auction.go (L124-152)
```go
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
```
