### Title
Auction Bid EIP-712 Signature Fails to Bind `MaxGasPrice` When Using the v2.1 Typehash - ([File: kaiax/auction/eip712.go])

### Summary
The Kaia auction module (`kaiax/auction`) computes the searcher's EIP-712 signing digest via `Bid.GetHashTypedData`, selecting between two different struct type-hashes (`auctionTypeHash` / `auctionTypeHashV3`) based on a `version` string. The `BidData.MaxGasPrice` field is only included in the signed struct when `version == AuctionVersionV3 ("0.0.2")`. For any other value — including the officially supported `AuctionVersionV2 ("0.0.1")` and the explicit "unknown version" fallback — the digest is computed over the v2.1 struct, which has no `maxGasPrice` field at all. This mirrors the CMS bug class in the report: a party that controls a supposedly out-of-band parameter (cipher/tag-length in CMS; `MaxGasPrice` here) can change that parameter after the message is "signed"/"authenticated" (recipientInfo in CMS; `SearcherSig` here) without invalidating the cryptographic check, because the algorithm/version selection decides what is actually covered by the authentication.

### Finding Description
`GetHashTypedData` builds the domain separator and struct hash from the caller-supplied `version` parameter: [1](#0-0) 

Note that the `auctionType` (v2.1) EIP-712 type string does not declare `maxGasPrice`: [2](#0-1) 
and `Bid.EncodeData()` (the v2.1 encoder) never serializes `MaxGasPrice`, only `bidV3.EncodeData()` does: [3](#0-2) 

`BidData` nonetheless carries a `MaxGasPrice` field alongside the signed fields: [4](#0-3) 

The bid pool validates the searcher's signature by calling `ValidateSearcherSig`, which forwards the pool's cached `auctionEntryPointVersion` straight into `GetHashTypedData`: [5](#0-4) [6](#0-5) 

Because `bp.auctionEntryPointVersion` is read from the on-chain `AuctionEntryPoint.AUCTION_VERSION()` and the pool falls back to the v2.1 typehash for any value other than the exact string `"0.0.2"`, any bid submitted while the pool considers the version to be `"0.0.1"` (or any unrecognized string) is signed and verified without `MaxGasPrice` ever being part of the authenticated digest. A searcher signs `SearcherSig` over a digest that says nothing about `MaxGasPrice`, yet `bid.MaxGasPrice` is still carried in `BidData` and reaches `auction_submitBid` via the public RPC (`kaiax/auction/impl/api.go`) and is used downstream by the auction bundling/execution logic (`blockchain/system/auction.go`) to influence auction economics. This is directly analogous to the CMS advisory's "recipientInfos intact, algorithm identifier swapped" attack: the authenticating signature (`recipientInfos`/`SearcherSig`) remains valid, while a value that should be protected (cipher mode/tag length vs. `MaxGasPrice`) is left outside the authenticated scope and can be freely altered by any party who relays or resubmits the bid — including the `Auctioneer` service itself, which per the module's own trust model is an external, independently-operated relay rather than part of the consensus-critical trusted base: [7](#0-6) 

### Impact Explanation
`MaxGasPrice` is intended to cap what a searcher is willing to pay/allow in the auction bid execution path (referenced in `blockchain/system/auction.go` and `kaiax/auction/impl/api.go`). If the pool is operating under the v2.1 typehash (the currently documented default, `AuctionVersionV2 = "0.0.1"`), any entity capable of intercepting or re-relaying a searcher's already-signed `Bid` — the `Auctioneer` relay, a malicious mempool/gossip participant for bid messages, or anyone resubmitting the bid via `auction_submitBid` — can rewrite `MaxGasPrice` to any value without invalidating `SearcherSig`, since that field is provably absent from the digest that was actually signed. This allows fee/economic-limit bypass or manipulation for the searcher's auction settlement, i.e., unauthorized value movement/fee abuse consistent with the analog class in scope (gasless/auction settlement theft, fee abuse).

### Likelihood Explanation
Reachable by an ordinary auction bidder (searcher) submitting a normal signed bid through the public `auction_submitBid` RPC; no privileged access, validator collusion, or p2p/consensus manipulation is required — only that the bid pool's cached `auctionEntryPointVersion` is anything other than the exact `"0.0.2"` string, which is the default/legacy configuration (`AuctionVersionV2`). This is a straightforward, deterministic consequence of the version-dependent typehash selection, not a probabilistic or hard-to-trigger condition.

### Recommendation
Always include `MaxGasPrice` (defaulting to zero) in the EIP-712 struct hash regardless of `version`, or reject/normalize unknown/legacy version strings instead of silently falling back to a struct hash that omits fields present in `BidData`. At minimum, the v2.1 type string and encoder should be updated to commit to every field carried in `BidData` that is later consumed by execution/pricing logic, closing the "field present in payload but absent from signed digest" gap.

### Proof of Confirmed vs Unconfirmed
Confirmed via code: the version-selected type-hash/EncodeData omit `MaxGasPrice` for non-V3 versions, and `ValidateSearcherSig`/`GetHashTypedData` are wired directly to the pool's `auctionEntryPointVersion` with a "default to v2.1" fallback for unrecognized values. Not fully confirmed (index does not show the full contents of `blockchain/system/auction.go` / `kaiax/auction/impl/api.go`, only that they reference `MaxGasPrice`): the exact downstream effect of a tampered `MaxGasPrice` on settlement/fee accounting. If precise confirmation of the downstream economic effect is needed, a Devin session with full repo access should inspect `blockchain/system/auction.go` and `kaiax/auction/impl/api.go` to trace how `MaxGasPrice` is enforced against the actual gas price paid.

### Citations

**File:** kaiax/auction/eip712.go (L26-28)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
```

**File:** kaiax/auction/eip712.go (L75-118)
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

// EncodeEIP712 encodes any EIP712Encoder according to EIP-712 specification
func EncodeEIP712(encoder EIP712Encoder) []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, encoder.EncodeType()...)
	encoded = append(encoded, encoder.EncodeData()...)
	return crypto.Keccak256Hash(encoded).Bytes()
}

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

**File:** kaiax/auction/eip712.go (L120-149)
```go
// GetHashTypedData returns the EIP-712 digest for the bid.
// version must match the on-chain AUCTION_VERSION of the entry-point contract
// the bid targets; "0.0.2" selects the v3.0 struct typehash (with maxGasPrice),
// any other value falls back to the v2.1 typehash.
func (b *Bid) GetHashTypedData(chainId *big.Int, verifyingContract common.Address, version string) []byte {
	if chainId == nil {
		return nil
	}

	domain := EIP712Domain{
		EIP712DomainTypeHash: eip712TypeHash,
		NameHash:             auctionNameHash,
		VersionHash:          crypto.Keccak256Hash([]byte(version)),
		ChainId:              chainId,
		VerifyingContract:    verifyingContract,
	}

	domainSeparator := EncodeEIP712(domain)

	var structHash []byte
	if version == AuctionVersionV3 {
		structHash = EncodeEIP712(bidV3{b})
	} else if version == AuctionVersionV2 {
		structHash = EncodeEIP712(b)
	} else {
		// Unknown version: default to v2.1 typehash.
		structHash = EncodeEIP712(b)
	}

	return crypto.Keccak256([]byte{0x19, 0x01}, domainSeparator, structHash)
```

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

**File:** kaiax/auction/README.md (L1-24)
```markdown
# kaiax/auction

This module implements the Kaia client (CN) for processing the auction specified by [KIP-249](https://kips.kaia.io/KIPs/kip-249).

## Concepts

The bid is a data that contains the information to generate a transaction to be executed right after the target transaction is executed. All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`.

![auction_topology](./auction_topology.png)

As shown in the topology, the `Auctioneer` won't connect to the PN or EN, and the auction module itself is disabled on PN and EN.

## Bid pool validation rules

A bid pool is responsible for managing the valid bids from the `Auctioneer`. The bid must satisfy the following rules:

1. The `bid.Sender` must not be in the winner list of the same block number if the new bid doesn't have the same target block and hash as the previous bid.
2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
3. The `bid.Bid` must be greater than 0.
4. The `bid.Data` size must be less than or equal to `BidTxMaxDataSize`.
5. The `bid.CallGasLimit` must be less or equal to `BidTxMaxCallGasLimit`.
6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.

Please note that `Auctioneer` also validates the searcher's bid according to the KIP-249.
```
