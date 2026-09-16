### Title
Auction bid's `MaxGasPrice` field is not covered by the EIP-712 searcher signature under the v2 typehash, allowing an unprivileged RPC caller to tamper with it after signing - (File: kaiax/auction/eip712.go)

### Summary
`auction_submitBid` accepts a `BidInput` struct from any caller (an "auctioneer" client, but ultimately reachable from any public-RPC caller since `SubmitBid` performs no additional authentication) that is copied verbatim into `auction.BidData`, including `MaxGasPrice`. `ValidateSearcherSig` verifies the bid only against an EIP-712 digest computed by `Bid.EncodeData()`/`GetHashTypedData()`. For `AuctionVersionV2` (and the "unknown version" fallback), the struct hash is computed by `(*Bid).EncodeData()`, which does not include `MaxGasPrice` at all — only `bidV3.EncodeData()` (used for `AuctionVersionV3`) folds `MaxGasPrice` into the signed digest.

### Finding Description
`BidData.MaxGasPrice` (`kaiax/auction/bid.go:39`) is meant to be a value that the searcher commits to and the auctioneer/searcher jointly authorize before the bid is admitted into the bid pool: [1](#0-0) 

`auction_submitBid`'s `BidInput` takes `MaxGasPrice` directly as an RPC parameter and `ToBid` copies it unchanged into `BidData` without any signature or trust check at this layer: [2](#0-1) 

The only integrity check performed before the bid is admitted is `bp.validateBidSigs`, which calls `bid.ValidateSearcherSig` and `bid.ValidateAuctioneerSig`: [3](#0-2) 

`ValidateSearcherSig` recovers the signer from `GetHashTypedData(chainId, verifyingContract, version)` and only asserts that the recovered address equals `b.Sender` — it does not by itself guarantee that every field of `BidData` was bound into the digest: [4](#0-3) 

The bug is in how the digest is constructed. For the v3 typehash, `MaxGasPrice` is explicitly encoded into the struct hash: [5](#0-4) 

But for the v2 typehash (and the "unknown version" fallback), `(*Bid).EncodeData()` — used by `GetHashTypedData` whenever `version != AuctionVersionV3` — omits `MaxGasPrice` from the fields it hashes: [6](#0-5) [7](#0-6)  <cite repo="bsaldua/kaia--023" path="kaiax/auction/eip712.go:120-149" start="139-147" end="139" />

Because `MaxGasPrice` is excluded from the v2 struct hash, a searcher's valid `SearcherSig`/`AuctioneerSig` pair generated for a v2 bid remains cryptographically valid for *any* value of `MaxGasPrice`. Anyone submitting `auction_submitBid` with a captured/replayed `(SearcherSig, AuctioneerSig)` pair can freely set or alter `MaxGasPrice` in the RPC payload without invalidating either signature — exactly analogous to the Ash CVE pattern where a value that is supposed to be fixed/authorized by a trusted party (there: a private action argument; here: a EIP-712-signed bid parameter) can instead be injected/overridden by the unprivileged caller of a public entry point (there: `for_create`/atomic changeset param map; here: the `auction_submitBid` JSON-RPC parameter), because the verification/filtering logic does not cover that specific field.

### Impact Explanation
`MaxGasPrice` in the auction/KIP-249 flow is used to bound the gas price permitted for bid execution/settlement of the winning bid. If an attacker (or a malicious relay sitting between the searcher and the node, or simply the searcher itself replaying/tampering after auctioneer approval) can set an arbitrary `MaxGasPrice` value that was never actually reviewed/signed-off by the searcher for that value, they can manipulate a value that should only be controlled by the trusted signing parties. Depending on how downstream bundle/settlement logic consumes `MaxGasPrice` when building the bid transaction bundle, this can result in fee/settlement abuse for the auction module or acceptance of a bid whose economic terms were never actually authorized by the signer — an integrity violation consistent with "fee or fee-delegation abuse" / "gasless or auction settlement theft" in the accepted-impact list.

### Likelihood Explanation
The `auction_submitBid` RPC is a standard, always-reachable public API entry point (`kaiax/auction/impl/api.go`), and `v2` (`AuctionVersionV2 = "0.0.1"`) is the pre-existing/default version whose typehash omits `maxGasPrice` (only v3, `0.0.2`, added the field to the signed struct). Any deployment still using the v2 `AuctionEntryPoint` (or where `EncodeAuctionCallData`/`DecodeAuctionCallData` falls into the non-v3 selector path, cf. `blockchain/system/auction.go`) is exposed. Exploitation requires only capturing one valid `(SearcherSig, AuctioneerSig)` pair for a v2 bid and resubmitting it with a modified `maxGasPrice` value — no cryptographic break is needed.

### Recommendation
Include `MaxGasPrice` in the EIP-712 struct hash for all supported auction versions (not just v3), or reject/zero out `MaxGasPrice` entirely when validating bids against the v2 typehash so a value excluded from the signed digest can never be trusted/consumed downstream. Ensure that any `BidData` field copied from `BidInput` in `kaiax/auction/impl/api.go` is always bound into the signature check for every version path, mirroring the fix pattern used by Ash (fully strip/ignore fields whose authenticity is not established, rather than allowing incomplete coverage to let them pass through unauthorized).

### Proof of Concept
1. A searcher constructs and EIP-712 signs a v2 `AuctionTx` bid (`AuctionVersionV2`) with `maxGasPrice = X` (or leaves it default/zero, since it is irrelevant to the v2 digest), obtains the auctioneer's approval signature over `SearcherSig`.
2. An attacker (or the searcher itself after the fact) intercepts/replays this `(SearcherSig, AuctioneerSig)` pair, and calls `auction_submitBid` with the same `BidInput` fields but a different `maxGasPrice = Y` (arbitrary value) in the JSON RPC payload: `kaiax/auction/impl/api.go` `SubmitBid` → `ToBid`.
3. `bp.validateBid` → `bp.validateBidSigs` → `bid.ValidateSearcherSig` recomputes the v2 digest via `(*Bid).EncodeData()`, which never includes `maxGasPrice`, so the recovered signer still matches `bid.Sender`; `bid.ValidateAuctioneerSig` only checks a hash of `SearcherSig` bytes, which are unchanged. Both checks pass.
4. The bid is admitted into the bid pool with the attacker-controlled `MaxGasPrice = Y`, which was never actually reviewed/authorized by the searcher or auctioneer for that value.

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

**File:** kaiax/auction/eip712.go (L26-28)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
```

**File:** kaiax/auction/eip712.go (L75-86)
```go
func (b *Bid) EncodeData() []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
}
```

**File:** kaiax/auction/eip712.go (L96-118)
```go
type bidV3 struct{ *Bid }

func (b bidV3) EncodeType() []byte {
	return auctionTypeHashV3.Bytes()
}

func (b bidV3) EncodeData() []byte {
	maxGasPrice := b.MaxGasPrice
	if maxGasPrice == nil {
		maxGasPrice = new(big.Int)
	}
	encoded := make([]byte, 0, 10*32)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.BidData.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(maxGasPrice.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
}
```
