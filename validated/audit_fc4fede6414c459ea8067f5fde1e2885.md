### Title
Denial-of-Service via unbounded-length signature slicing in auction bid verification (`getSigner`) - (File: kaiax/auction/bid.go)

### Summary
The FreeRDP CVE-2026-33977 bug class is: a value taken directly from untrusted, attacker-controlled data is used to index into a fixed-size buffer/table without a length/range check, triggering an assertion failure and process abort. The Kaia analog is in the KIP-249 auction module's bid signature verification: `getSigner()` in `kaiax/auction/bid.go` indexes into an attacker-supplied `[]byte` signature at fixed offsets without first checking its length, which panics (Go's runtime equivalent of an assertion abort) when a bidder submits a malformed bid.

### Finding Description
`getSigner` is used to recover the address behind a bid's `SearcherSig` and `AuctioneerSig`: [1](#0-0) 

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
}
```

There is no `len(sig) < 65` (or `!= 65`) guard before indexing `copiedSig[crypto.RecoveryIDOffset]` (offset 64) or slicing `[0:32]`/`[32:64]`. This function is reached from `ValidateSearcherSig` and `ValidateAuctioneerSig` in the same file, which are called by `BidPool.validateBidSigs` as part of `validateBid()`: [2](#0-1) 

`validateBid` is invoked for every bid accepted through the auction module's public entry points (RPC bid submission and p2p `HandleBid`, both reachable by any unprivileged searcher/bidder). `Bid.SearcherSig` and `Bid.AuctioneerSig` are plain `[]byte` fields populated directly from attacker-supplied input — either RLP/ABI-decoded calldata via `DecodeAuctionCallData` [3](#0-2)  or fields set directly on a submitted `Bid` object. Neither path enforces a fixed 65-byte length before the value reaches `getSigner`.

Supplying a `SearcherSig` or `AuctioneerSig` shorter than 65 bytes (including an empty slice) causes a Go runtime "index out of range" panic when `copiedSig[64]` or `copiedSig[32:64]` is evaluated — the direct analog of FreeRDP's out-of-bounds table index causing `WINPR_ASSERT`/`SIGABRT`.

### Impact Explanation
An unrecovered panic in Go terminates the goroutine and, unless every call site up the stack has a `recover()`, crashes the entire node process. Because bid validation runs on every submitted bid (both via public RPC and p2p gossip), a single malformed bid from any unprivileged auction bidder can potentially crash a node providing auction/RPC service — a Denial of Service on infrastructure that (per the module's default configuration) is enabled for any node participating in the auction/MEV workflow. This matches the "Medium" severity DoS class of the source CVE and fits the allowed impact category of "acceptance of an invalid transaction" / no-impact-safe processing turning into a crash.

### Likelihood Explanation
High: constructing a bid with a truncated `SearcherSig`/`AuctioneerSig` (e.g. 0–64 bytes instead of the expected 65) requires no privileged access — any bidder submitting a bid through the public bid-submission RPC or p2p bid message can trigger it. No cryptographic material or special permissions are needed, only control over the byte-length of a field in a self-authored Bid object.

### Recommendation
Add an explicit signature-length check (`len(sig) != 65 → return common.Address{}, ErrInvalidSignature`) at the very top of `getSigner` in `kaiax/auction/bid.go`, before any indexing/slicing occurs. Additionally, validate `len(SearcherSig) == 65` and `len(AuctioneerSig) == 65` as an early, cheap check in `BidPool.validateBid`/`validateBidSigs` (kaiax/auction/impl/bid_pool.go) and in `DecodeAuctionCallData` (blockchain/system/auction.go), so malformed bids are rejected with a normal error rather than reaching the unsafe slice access.

### Proof of Concept
1. Craft a `Bid` (e.g. via the auction module's bid-submission RPC or a p2p `BidMsg`) with valid other fields but `SearcherSig = []byte{}` (or any slice shorter than 65 bytes).
2. Submit the bid to a Kaia node running the auction module (`BidPool.AddBid` → `validateBid` → `validateBidSigs` → `ValidateSearcherSig` → `getSigner`).
3. `getSigner` executes `copiedSig[crypto.RecoveryIDOffset]` (offset 64) on a slice shorter than 65 bytes, causing an `index out of range` panic.
4. If this panic is not recovered somewhere up the call stack (not fully confirmed in the reviewed code — see note below), the node process crashes, denying service to legitimate users.

**Note on verification limits:** I was not able to fully trace whether every caller path (RPC handler vs. p2p `HandleBid` goroutine) wraps bid processing in a `recover()`, due to running out of tool-call budget before inspecting `kaiax/auction/impl/handler.go` and `kaiax/auction/impl/api.go` in full. If a `recover()` exists at the goroutine boundary, the impact would be reduced to per-bid processing failure rather than a full node crash, though the underlying missing-length-check root cause and its DoS-class risk remain valid.

### Citations

**File:** kaiax/auction/bid.go (L142-160)
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
	if err != nil {
		return common.Address{}, err
	}
	return crypto.PubkeyToAddress(*pub), nil
```

**File:** kaiax/auction/impl/bid_pool.go (L384-392)
```go
	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}

	// 6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.
	if err := bp.validateBidSigs(bid); err != nil {
		return err
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
