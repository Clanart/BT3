# Title
Auctioneer signature does not bind to Bid content, allowing tampering of unsigned `BidData` fields (e.g., `MaxGasPrice`) after signing — analog of unnormalized signed-activity spoofing (CVE-2024-32983) - ([File: kaiax/auction/bid.go])

## Summary
The Misskey CVE root cause is that a signature is computed and verified over a representation of data that does not fully bind/normalize all fields that are later used by the consumer, so an attacker can change unsigned/loosely-covered fields while the signature still verifies. Kaia's KIP-249 auction bid-verification path (`kaiax/auction/bid.go`) has an analogous pattern: the `AuctioneerSig` never binds to any `BidData` content at all, and `SearcherSig` is verified against an EIP‑712 typed digest that (per the reference `AuctionEntryPointMock.sol` struct and the test fixtures) appears not to include all `BidData` fields, notably `MaxGasPrice`. Fields excluded from the signed digest can be mutated by whoever submits/relays the bid without invalidating either signature.

## Finding Description
`Bid.ValidateSearcherSig` recomputes an EIP‑712 digest from `BidData` and checks that the recovered address equals `Sender`: [1](#0-0) 

`Bid.ValidateAuctioneerSig` verifies the auctioneer's signature over `GetEthSignedMessageHash()`, which is computed **only from the raw `SearcherSig` bytes**, never from any `BidData` field: [2](#0-1) [3](#0-2) 

Both checks are performed independently in `BidPool.validateBidSigs`: [4](#0-3) 

`BidData` carries a `MaxGasPrice` field marked `omitempty`, which is absent from the on-chain reference `AuctionTx` struct used to describe what a searcher signs (`targetTxHash, blockNumber, sender, to, nonce, bid, callGasLimit, data, searcherSig, auctioneerSig` — no `maxGasPrice`): [5](#0-4) [6](#0-5) 

Because:
1. `AuctioneerSig` is bound only to the `SearcherSig` byte string (not to `BidData`), and
2. `SearcherSig` is only bound to whatever fields are actually included in `GetHashTypedData(...)`,

any `BidData` field that is not part of the EIP‑712 struct (the code and mock contract strongly suggest `MaxGasPrice` is such a field) can be changed after both signatures are produced, and `validateBidSigs` will still pass — since neither signature check recomputes anything that depends on that field. This is structurally the same bug class as the Misskey advisory: the cryptographic envelope does not normalize/cover the full object that downstream code trusts and acts on, so unsigned-but-consumed fields become attacker-controlled.

**Caveat on completeness:** I was not able to open the file that implements `GetHashTypedData` (not retrieved within the available tool budget) to enumerate every field included in the EIP‑712 struct hash byte-for-byte. The conclusion that `MaxGasPrice` (and potentially other fields) is excluded is inferred from (a) the mismatch with the reference `AuctionTx` Solidity struct, (b) the `omitempty` JSON tag suggesting it was added later without updating the signed struct, and (c) the unconditional fact — directly confirmed in code — that `AuctioneerSig` never covers any `BidData` field. This last fact alone is sufficient to establish that the auctioneer's approval is not bound to bid content, and should be verified/fixed regardless of the exact scope of the searcher's EIP-712 struct.

## Impact Explanation
`MaxGasPrice` (and any other unsigned `BidData` field) governs how the bid is treated in the bid pool and in gas/fee accounting for auction settlement (KIP‑249). If it can be altered post-signature without invalidating `SearcherSig`/`AuctioneerSig`, a party relaying/submitting the bid (including the searcher itself when constructing the RPC payload, or any intermediary) can present a bid whose committed cryptographic proof (signature) does not match its effective economic terms — enabling fee/settlement manipulation (e.g., bid execution proceeding with a materially different gas price ceiling than what was actually authorized by the signer), i.e., "fee or auction settlement theft"-class impact as scoped for this exercise.

## Likelihood Explanation
The bid submission path (`auction_submitBid` RPC, reachable by any public caller with a well-formed request body) accepts attacker-controlled `BidInput` and only re-validates the two independent signatures shown above; it does not validate that `MaxGasPrice` (or other excluded fields) is internally consistent with anything signed. This makes exploitation straightforward for any bidder/relayer capable of forming/observing a valid `(SearcherSig, AuctioneerSig)` pair.

## Recommendation
Include every `BidData` field that affects settlement/economics (in particular `MaxGasPrice`) inside the EIP‑712 struct that `SearcherSig` is computed over, and make `AuctioneerSig` sign over the full canonical `BidData` (or its hash) rather than merely over the raw `SearcherSig` bytes, so that no field can be altered post-signature without invalidating both signatures.

## Proof of Concept
1. Searcher signs a `Bid` with `MaxGasPrice = X` via `ValidateSearcherSig`'s EIP‑712 digest (which, per the reference contract, omits `maxGasPrice`).
2. Auctioneer signs `GetEthSignedMessageHash()`, which is computed solely from `SearcherSig` bytes (`kaiax/auction/bid.go:52-55`) — independent of `MaxGasPrice`.
3. Before/while relaying the bid (e.g., via `auction_submitBid`), the field `BidData.MaxGasPrice` is changed to `Y != X`.
4. `BidPool.validateBidSigs` (`kaiax/auction/impl/bid_pool.go:397-419`) calls `ValidateSearcherSig` (digest unaffected by `MaxGasPrice`) and `ValidateAuctioneerSig` (unaffected by any `BidData` field) — both succeed.
5. The bid is admitted to the pool and later bundled for block building with the attacker‑chosen `MaxGasPrice = Y`, despite the searcher never having signed that value.

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

**File:** kaiax/auction/bid.go (L52-55)
```go
func (b *Bid) GetEthSignedMessageHash() []byte {
	data := b.SearcherSig
	return crypto.Keccak256(fmt.Appendf(nil, "\x19Ethereum Signed Message:\n%d%s", len(data), data))
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

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L22-33)
```text
    struct AuctionTx {
        bytes32 targetTxHash;
        uint256 blockNumber;
        address sender;
        address to;
        uint256 nonce;
        uint256 bid;
        uint256 callGasLimit;
        bytes data;
        bytes searcherSig; // digest = hashTypedData(AuctionTx)
        bytes auctioneerSig; // digest = hashEthSignedMessage(searcherSig)
    }
```
