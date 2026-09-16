### Title
Unrecognized/future `AUCTION_VERSION` silently falls back to v2.1 ABI and typehash, causing permanent DoS of bid submission and signature verification - (File: kaiax/auction/eip712.go, blockchain/system/auction.go)

### Summary
The Kaia auction module dynamically reads the on-chain `AUCTION_VERSION()` string from the active `AuctionEntryPoint` contract and uses it to select which Go struct/ABI (`IAuctionEntryPointAuctionTx`) and EIP-712 typehash to use when encoding a bid's `call(...)` transaction and when verifying/generating the searcher's EIP-712 signature. Any version string other than the two hardcoded values `"0.0.1"` (v2.1) and `"0.0.2"` (v3.0) is silently treated as v2.1, both for calldata encoding and for signature typehash selection. This mirrors the reported bug class: an inappropriate/mismatched struct definition being used to ABI-encode a call to an external contract function, causing every call to permanently revert.

### Finding Description
`ReadAuctionVersion` reads `AUCTION_VERSION()` from the active entry-point contract [1](#0-0) , and `EncodeAuctionCallData` picks the struct/ABI used to `Pack("call", input)` based on that string, defaulting to the v2.1 `IAuctionEntryPointAuctionTx` (without `MaxGasPrice`) for "any other value" besides `AuctionVersionV3` [2](#0-1) .

Symmetrically, `GetHashTypedData` in the EIP-712 signing/verification code defaults to the v2.1 typehash (`auctionType`, without `maxGasPrice`) whenever the version is not exactly `AuctionVersionV2` or `AuctionVersionV3` [3](#0-2) . The two known version constants are hardcoded as `"0.0.1"`/`"0.0.2"` [4](#0-3) .

The version obtained on-chain is fed straight into both the calldata builder (used to build the node's own bid-submission transaction) and the bid pool's signature validator without any explicit "supported version" allow-list check:
- `updateAuctionInfo` reads `AUCTION_VERSION` every block and stores it unconditionally [5](#0-4) .
- `GetBidTxGenerator` calls `system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())` to build the transaction data sent to the entry point [6](#0-5) .
- `validateBidSigs` calls `bid.ValidateSearcherSig(..., bp.auctionEntryPointVersion)`, which internally calls `GetHashTypedData` with the same version string [7](#0-6) , [8](#0-7) .

If governance activates a future `AuctionEntryPoint` version (e.g. `"0.0.3"`) whose on-chain `AuctionTx` struct/`call(...)` selector differs from both hardcoded structs (analogous to how v3.0 added `maxGasPrice` to v2.1's layout), the node will:
1. Encode bid-submission calldata using the stale v2.1 struct/selector via `EncodeAuctionCallData`, which will not match the deployed contract's actual `call(...)` function signature, causing the transaction to revert on every submission — the same "wrong struct layout → function reverts" root cause as the reported LooksRare `exactOutputSingle` bug.
2. Compute EIP-712 digests using the stale v2.1 typehash via `GetHashTypedData`, causing signature verification to be computed against the wrong struct definition.

### Impact Explanation
Every unprivileged auction bidder ("searcher") transaction targeting the new entry-point version would have its bid-submission transaction (built and signed by the node itself via `GetBidTxGenerator`) permanently revert on-chain, and/or have correctly-signed bids rejected by `validateBidSigs` due to typehash mismatch. Because the fallback is silent (no error returned, no explicit rejection of unknown versions), this is a Permanent DoS of the entire auction subsystem following a routine version bump, without any code fix required to trigger it. This matches "acceptance of an invalid transaction" / DoS of auction settlement functionality.

### Likelihood Explanation
This requires governance/operators to activate a new `AuctionEntryPoint` contract version with a modified `AuctionTx` struct while the go-kaia binary still only recognizes `"0.0.1"`/`"0.0.2"`. This is a normal upgrade path (the codebase already anticipates multiple versions, as evidenced by v2.1/v3.0 support existing side-by-side), so a future v4 rollout without a corresponding node software upgrade is a realistic and likely-recurring event, not a contrived edge case.

### Recommendation
Replace the "default to v2.1" fallback in both `EncodeAuctionCallData` (blockchain/system/auction.go) and `GetHashTypedData`/`bid.ValidateSearcherSig` (kaiax/auction/eip712.go, kaiax/auction/bid.go) with an explicit error when the on-chain `AUCTION_VERSION()` does not match a known, supported version constant. In `updateAuctionInfo`, treat an unsupported version the same as a zero auctioneer/entry-point address (i.e., stop the auction) rather than silently falling back to an old struct/typehash, so that an unrecognized version fails safe instead of producing permanently-reverting transactions or invalid-typehash signature checks.

### Proof of Concept
1. Deploy (via governance) a new `AuctionEntryPoint` contract whose `call(...)` function takes an `AuctionTx` struct with an additional field (e.g., adding `priorityFee` after `maxGasPrice`), and set `AUCTION_VERSION() = "0.0.3"`.
2. Activate it via the system contract registry so `system.ReadActiveAddressFromRegistry(..., system.AuctionEntryPointName, ...)` returns its address.
3. Node's `updateAuctionInfo` reads `auctionEntryPointVersion = "0.0.3"` and stores it unconditionally [9](#0-8) .
4. A searcher submits a bid; `GetBidTxGenerator` calls `EncodeAuctionCallData(bid, "0.0.3")`, which falls into the `else` branch and packs the bid using the v2.1 (10-field, no `maxGasPrice`) struct/selector [10](#0-9) .
5. The resulting transaction's function selector/calldata does not match the deployed v4 contract's `call(...)` signature, so the transaction reverts on-chain every time — permanently blocking auction settlement for any bid, until node software is patched with the new struct definition.

### Citations

**File:** blockchain/system/auction.go (L64-77)
```go
// ReadAuctionVersion reads the AUCTION_VERSION view from the given entry-point
// contract. The result distinguishes v2.1 ("0.0.1") from v3.0 ("0.0.2") so
// callers can pick the appropriate EIP-712 typehash and ABI.
//
// The v3.0 binding is used for the call: AUCTION_VERSION() is a public-constant
// getter with the same selector on both v2.1 and v3.0 contracts, so the same
// typed binding works against either deployment.
func ReadAuctionVersion(backend bind.ContractCaller, contractAddr common.Address, num *big.Int) (string, error) {
	caller, err := contractsv3.NewIAuctionEntryPointCaller(contractAddr, backend)
	if err != nil {
		return "", err
	}
	return caller.AUCTIONVERSION(&bind.CallOpts{BlockNumber: num})
}
```

**File:** blockchain/system/auction.go (L79-119)
```go
// EncodeAuctionCallData encodes the bid as calldata for the active auction
// entry-point contract. version must match the on-chain AUCTION_VERSION of the
// active contract; "0.0.2" selects the v3.0 ABI (with maxGasPrice), any other
// value falls back to the v2.1 ABI.
func EncodeAuctionCallData(bid *auction.Bid, version string) ([]byte, error) {
	if version == auction.AuctionVersionV3 {
		maxGasPrice := bid.MaxGasPrice
		if maxGasPrice == nil {
			maxGasPrice = new(big.Int)
		}
		input := contractsv3.IAuctionEntryPointAuctionTx{
			TargetTxHash:  bid.TargetTxHash,
			BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
			Sender:        bid.Sender,
			To:            bid.To,
			Nonce:         new(big.Int).SetUint64(bid.Nonce),
			Bid:           bid.Bid,
			MaxGasPrice:   maxGasPrice,
			CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
			Data:          bid.Data,
			SearcherSig:   bid.SearcherSig,
			AuctioneerSig: bid.AuctioneerSig,
		}
		return abiV3.Pack("call", input)
	}

	// auction.AuctionVersionV2 or unknown version: default to v2.1 ABI.
	input := contracts.IAuctionEntryPointAuctionTx{
		TargetTxHash:  bid.TargetTxHash,
		BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
		Sender:        bid.Sender,
		To:            bid.To,
		Nonce:         new(big.Int).SetUint64(bid.Nonce),
		Bid:           bid.Bid,
		CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
		Data:          bid.Data,
		SearcherSig:   bid.SearcherSig,
		AuctioneerSig: bid.AuctioneerSig,
	}
	return abi.Pack("call", input)
}
```

**File:** kaiax/auction/eip712.go (L26-36)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
	EIP712DomainType = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
	auctionName      = "KAIA_AUCTION"
	// Auction version strings match the on-chain AUCTION_VERSION view of the
	// corresponding entry-point contract. v3.0 selects the typehash that
	// includes maxGasPrice.
	AuctionVersionV2 = "0.0.1"
	AuctionVersionV3 = "0.0.2"
)
```

**File:** kaiax/auction/eip712.go (L120-150)
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
}
```

**File:** kaiax/auction/impl/execution.go (L96-109)
```go
	// 3. Read gas buffer estimate
	bidTxGasBuffer, err = system.ReadGasBufferEstimate(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	// 4. Read AUCTION_VERSION to pick the right EIP-712 typehash and ABI for bids targeting this entry point.
	auctionEntryPointVersion, err = system.ReadAuctionVersion(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	return true
}
```

**File:** kaiax/auction/impl/getter.go (L27-39)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
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
