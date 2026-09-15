### Title
Silent v2.1 fallback in auction bid encoding/hashing drops searcher's MaxGasPrice protection without error - ([File: blockchain/system/auction.go], [File: kaiax/auction/eip712.go])

### Summary
When a searcher submits a KIP-249 auction bid with `MaxGasPrice` set (v3.0 protection), the code silently falls back to the v2.1 encoding/typehash — which has no `MaxGasPrice` field — whenever the resolved `AuctionEntryPoint` version string is not exactly `"0.0.2"` (e.g., empty or any unexpected value), instead of erroring out. This mirrors the reported bug class: an explicit user option/intent (`MaxGasPrice` cap, analogous to `opts.transferfCash`) is silently discarded and a different, unprotected code path executes, with no error surfaced to the submitter.

### Finding Description
`EncodeAuctionCallData` only uses the v3.0 ABI (which includes `MaxGasPrice`) when `version == auction.AuctionVersionV3`; for any other value — including an empty string or a stale/mismatched version — it silently drops to the v2.1 ABI and simply omits `MaxGasPrice` entirely: [1](#0-0) 

The same silent-fallback pattern exists in the EIP-712 signature hashing used to validate bids: `GetHashTypedData` only uses the `bidV3` (with `MaxGasPrice`) struct hash when version is exactly `AuctionVersionV3`; any other value (including unknown/empty) defaults to the v2.1 typehash without `MaxGasPrice`, and no error is returned: [2](#0-1) 

This fallback-without-error behavior is explicitly acknowledged in test comments/names such as `"empty version falls back to v2.1 ABI"`: [3](#0-2) 

At block-building time, `GetBidTxGenerator` calls `EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())` to build the actual on-chain `BidTx` calldata sent to the `AuctionEntryPoint`: [4](#0-3) 

The `AuctionEntryPointVersion` tracked in the bid pool is read from the chain (`ReadAuctionVersion`) and can change independently of when a searcher submitted their bid with `MaxGasPrice` set, since it's asynchronously refreshed as `SystemRegistry`/`AuctionEntryPoint` state changes: [5](#0-4) 

Because both the bid-pool's stored version and the searcher's originally-signed version are compared only via exact string equality with a "default to v2.1" else-branch rather than a strict validation/error path, any mismatch (version rollback, race during a v2.1→v3.0 upgrade window, or an empty/garbage version value returned from the entry point) causes the `MaxGasPrice` field to be silently stripped from both the signature struct hash and the on-chain calldata — the bid is processed as if the searcher never specified a gas-price cap, without any error being raised to the searcher.

### Impact Explanation
`MaxGasPrice` is a searcher-specified value that caps how much gas price the proposer/auctioneer flow is authorized to apply on the searcher's behalf during KIP-249 auction settlement. Silently dropping it converts a bounded-cost transaction into an effectively unbounded one from the searcher's perspective, without any indication of failure — directly analogous to the referenced report where an explicit user directive (asset transfer) is replaced by different, unintended on-chain behavior (cash-out) with no error. This can result in unauthorized excess fee extraction from the searcher's bid/settlement flow, i.e., fee-delegation/auction-settlement abuse impacting value the searcher explicitly tried to protect.

### Likelihood Explanation
This requires a version mismatch between when a bid is signed/submitted (client-side, using `AuctionVersionV3`) and when it is encoded/hashed for on-chain use (using the bid pool's cached `auctionEntryPointVersion`), which is plausible during any window where the on-chain `AuctionEntryPoint`/`SystemRegistry` version is upgraded, rolled back, or momentarily returns an unexpected/empty string — a condition reachable without any privileged access, since it depends only on normal chain state transitions and asynchronous version refresh timing rather than an attacker needing special permissions.

### Recommendation
Replace the "default to v2.1 on any non-exact-match version" fallback in both `EncodeAuctionCallData`/`DecodeAuctionCallData` (`blockchain/system/auction.go`) and `GetHashTypedData` (`kaiax/auction/eip712.go`) with strict version matching: if `bid.MaxGasPrice != nil` (or the version passed is not one of the recognized, expected values), return an explicit error instead of silently using the v2.1 path. This ensures a bid carrying `MaxGasPrice` protection either encodes/validates using the v3.0 format or fails loudly, matching the confirmed mitigation direction from the referenced report (throw an error instead of silently substituting different behavior).

### Proof of Concept
1. A searcher submits a bid with `MaxGasPrice` set, signing it under `AuctionVersionV3` ("0.0.2") via `GetHashTypedData`.
2. At the moment the bid pool builds the `BidTx` (`GetBidTxGenerator` → `EncodeAuctionCallData`), `a.bidPool.GetAuctionEntryPointVersion()` returns something other than exactly `"0.0.2"` (e.g., "" during a version-refresh race, or a stale cached value after an on-chain rollback/upgrade).
3. `EncodeAuctionCallData` falls into the default branch, building v2.1 calldata with no `MaxGasPrice` field at all — see the `"empty version falls back to v2.1 ABI"` test case demonstrating this exact fallback with no error: [6](#0-5) 
4. The resulting `BidTx` is submitted on-chain without any `MaxGasPrice` enforcement, and no error is returned to the searcher informing them their cap was dropped.

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

**File:** blockchain/system/auction_test.go (L89-120)
```go
func TestEncodeDecodeAuctionCallData(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)

	v3Bid := &auction.Bid{BidData: data}
	v3Bid.MaxGasPrice = big.NewInt(1000000000)
	v2CallData := common.Hex2Bytes("ca1575540000000000000000000000000000000000000000000000000000000000000020cf5879724726228474db71caf83955a61341f2e5bacaa2b9d37f6e5f48241cbc000000000000000000000000000000000000000000000000000000000000000b00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c80000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f87570700000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008ac7230489e8000000000000000000000000000000000000000000000000000000000000009896800000000000000000000000000000000000000000000000000000000000000140000000000000000000000000000000000000000000000000000000000000018000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000 ... (truncated)
	v3CallData := common.Hex2Bytes("0e4fc4b20000000000000000000000000000000000000000000000000000000000000020cf5879724726228474db71caf83955a61341f2e5bacaa2b9d37f6e5f48241cbc000000000000000000000000000000000000000000000000000000000000000b00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c80000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f87570700000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008ac7230489e80000000000000000000000000000000000000000000000000000000000003b9aca000000000000000000000000000000000000000000000000000000000000989680000000000000000000000000000000000000000000000000000000000000016000000000000000000000000000000000000000000000000000000000000001a00000000000000000000000000000000000000000000000000000000 ... (truncated)

	tcs := []struct {
		name     string
		bid      *auction.Bid
		version  string
		expected []byte
	}{
		{
			"empty version falls back to v2.1 ABI",
			testBid,
			"",
			v2CallData,
		},
		{
			"explicit v2.1 version",
			testBid,
			auction.AuctionVersionV2,
			v2CallData,
		},
		{
			"v3.0 version (MaxGasPrice set)",
			v3Bid,
			auction.AuctionVersionV3,
			v3CallData,
		},
```

**File:** kaiax/auction/impl/getter.go (L27-47)
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

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}
```
